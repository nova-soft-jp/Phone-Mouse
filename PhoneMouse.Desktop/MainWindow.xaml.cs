using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Media.Animation;
using System.Windows.Forms;
using Microsoft.Win32;
using Wpf.Ui.Appearance;
using System.Diagnostics;

namespace PhoneMouse.Desktop;

public partial class MainWindow : Wpf.Ui.Controls.FluentWindow
{
    private readonly PhoneMouseServer server = new();
    private NotifyIcon? tray;
    private bool exiting;
    private bool connected;
    private bool menuOpen;
    private bool darkTheme = true;
    private bool loadingStartupOption;
    // XAML initialization fires Checked events for initially checked boxes.
    // Keep preference persistence disabled until InitializeComponent finishes.
    private bool loadingNotificationPreferences = true;
    private bool notifyConnection = true;
    private bool notifyDisconnect = true;
    private bool notifyError = true;
    public MainWindow()
    {
        InitializeComponent();
        LoadThemePreference();
        ApplyTheme(darkTheme, false);
        loadingNotificationPreferences = true;
        LoadNotificationPreferences();
        NotifyConnectionCheck.IsChecked = notifyConnection;
        NotifyDisconnectCheck.IsChecked = notifyDisconnect;
        NotifyErrorCheck.IsChecked = notifyError;
        loadingNotificationPreferences = false;
        loadingStartupOption = true;
        AutoStartCheck.IsChecked = IsAutoStartEnabled();
        loadingStartupOption = false;
        Loaded += OnLoaded;
        Closing += OnClosing;
        SystemEvents.DisplaySettingsChanged += OnDisplayChanged;
        server.Updated += OnServerUpdated;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        try { CreateTray(); await server.StartAsync(); }
        catch (Exception ex) { OnServerUpdated(new(server.Current.Url, server.Current.Qr, false, $"サーバーを開始できませんでした: {ex.Message}", true)); }
    }

    private void CreateTray()
    {
        var menu = new ContextMenuStrip();
        menu.Items.Add("Phone-Mouseを表示", null, (_, _) => RestoreWindow());
        menu.Items.Add("終了", null, OnTrayExit);
        tray = new NotifyIcon { Icon = CreateStatusIcon(false), Text = "Phone-Mouse - 未接続", ContextMenuStrip = menu, Visible = true };
        tray.MouseClick += (_, e) => { if (e.Button == MouseButtons.Left) RestoreWindow(); };
    }

    private async void OnTrayExit(object? sender, EventArgs e)
    {
        if (exiting) return;
        exiting = true;
        Hide();
        if (tray is not null) tray.Visible = false;
        server.Updated -= OnServerUpdated;
        try
        {
            await server.DisposeAsync();
        }
        catch (Exception ex)
        {
            Trace.TraceError("Phone-Mouse shutdown: {0}", ex);
        }
        finally
        {
            System.Windows.Application.Current.Shutdown();
        }
    }

    private void OnServerUpdated(ServerUpdate update)
    {
        Dispatcher.BeginInvoke(() =>
        {
            if (exiting) return;
            UrlText.Text = update.Url;
            AddressText.Text = Uri.TryCreate(update.Url, UriKind.Absolute, out var addressUri)
                ? addressUri.Authority
                : "接続先を確認できません";
            ConnectionHint.Text = update.Connected
                ? "接続中です。スマホを操作できます。"
                : update.IsError && !string.IsNullOrWhiteSpace(update.Notice)
                    ? update.Notice ?? "接続を開始できませんでした。ネットワーク設定を確認してください。"
                    : "スマホも同じWi-Fiに接続してください。VPNやゲストWi-Fiで届かない場合は、PCのQRを更新して読み直します。";
            ConnectionStageText.Text = update.SignalBars > 0
                ? "③ 操作接続完了"
                : update.PhonePageOpened
                    ? "② スマホがページを開きました。操作接続を確認中です。"
                    : "① QRコード表示中。スマホで読み取ってください。";
            if (update.Qr.Length > 0)
            {
                using var ms = new MemoryStream(update.Qr);
                var bitmap = new BitmapImage(); bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad; bitmap.StreamSource = ms; bitmap.EndInit(); bitmap.Freeze(); QrImage.Source = bitmap;
            }
            connected = update.Connected;
            SignalBar1.Visibility = update.SignalBars >= 1 ? Visibility.Visible : Visibility.Collapsed;
            SignalBar2.Visibility = update.SignalBars >= 2 ? Visibility.Visible : Visibility.Collapsed;
            SignalBar3.Visibility = update.SignalBars >= 3 ? Visibility.Visible : Visibility.Collapsed;
            var dotColor = update.Connected ? System.Windows.Media.Color.FromRgb(55, 205, 125) : System.Windows.Media.Color.FromRgb(240, 82, 82);
            var textColor = update.Connected ? System.Windows.Media.Color.FromRgb(91, 224, 153) : System.Windows.Media.Color.FromRgb(248, 113, 113);
            StatusDot.Fill = new SolidColorBrush(dotColor);
            MenuStatusDot.Fill = new SolidColorBrush(dotColor);
            StatusText.Text = MenuStatusText.Text = update.Connected ? "接続中" : "未接続";
            StatusText.Foreground = MenuStatusText.Foreground = new SolidColorBrush(textColor);
            if (tray is not null)
            {
                var oldIcon = tray.Icon;
                tray.Icon = CreateStatusIcon(update.Connected);
                oldIcon?.Dispose();
                tray.Text = update.Connected ? "Phone-Mouse - 接続中" : "Phone-Mouse - 未接続";
            }
            if (update.Notice is not null && tray is not null && ShouldShowNotice(update))
                tray.ShowBalloonTip(3000, update.IsError ? "Phone-Mouse - エラー" : "Phone-Mouse", update.Notice, update.IsError ? ToolTipIcon.Error : ToolTipIcon.Info);
        });
    }

    private bool ShouldShowNotice(ServerUpdate update)
    {
        if (update.IsError) return notifyError;
        if (update.IsConnectionNotice) return notifyConnection;
        if (update.IsDisconnectNotice) return notifyDisconnect;
        return true;
    }

    private static Icon CreateStatusIcon(bool isConnected)
    {
        using var iconStream = typeof(MainWindow).Assembly.GetManifestResourceStream("PhoneMouse.Desktop.assets.phone_mouse.png")
            ?? throw new InvalidOperationException("アプリアイコンを読み込めません。");
        using var source = new Bitmap(iconStream);
        using var bitmap = new Bitmap(source, 32, 32);
        using (var graphics = Graphics.FromImage(bitmap))
        using (var dot = new System.Drawing.SolidBrush(isConnected ? System.Drawing.Color.LimeGreen : System.Drawing.Color.Red))
        using (var outline = new System.Drawing.Pen(System.Drawing.Color.White, 1.5f))
        {
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.FillEllipse(dot, 20, 20, 11, 11);
            graphics.DrawEllipse(outline, 20, 20, 11, 11);
        }
        var handle = bitmap.GetHicon();
        try { using var temporary = System.Drawing.Icon.FromHandle(handle); return (System.Drawing.Icon)temporary.Clone(); }
        finally { DestroyIcon(handle); }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(nint handle);

    private async void RefreshQr_Click(object sender, RoutedEventArgs e) => await server.RefreshAsync();
    private void CopyUrl_Click(object sender, RoutedEventArgs e) { if (!string.IsNullOrWhiteSpace(UrlText.Text)) { System.Windows.Clipboard.SetText(UrlText.Text); if (tray is not null) tray.ShowBalloonTip(1800, "Phone-Mouse", "URLをコピーしました", ToolTipIcon.Info); } }
    private void Nav_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not System.Windows.Controls.Button b) return;
        var key = b.Tag?.ToString() ?? "home";
        if (key == "report")
        {
            SetMenuOpen(false);
            try
            {
                Process.Start(new ProcessStartInfo("https://github.com/nova-soft-jp/Phone-Mouse/issues/new?template=bug_report.yml") { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                if (notifyError) tray?.ShowBalloonTip(3000, "Phone-Mouse", $"報告フォームを開けませんでした: {ex.Message}", ToolTipIcon.Error);
            }
            return;
        }
        HomeView.Visibility = key == "home" ? Visibility.Visible : Visibility.Collapsed;
        DetailView.Visibility = key is not ("home" or "settings") ? Visibility.Visible : Visibility.Collapsed;
        SettingsView.Visibility = key == "settings" ? Visibility.Visible : Visibility.Collapsed;
        if (key is not ("home" or "settings")) (DetailTitle.Text, DetailText.Text) = key switch
        {
            "guide" => ("接続ガイド", "1. PCとスマートフォンを同じWi-Fiに接続します。\n\n2. スマートフォンのカメラでホーム画面のQRコードを読み取り、表示されたURLを開きます。\n\n3. 接続中の表示になれば準備完了です。\n\n接続できない場合は、PCとスマートフォンが同じネットワークにいるか、WindowsのファイアウォールでPhone-Mouseが許可されているか確認してください。QRコードを更新すると古いリンクは使えなくなります。"),
            "controls" => ("スマートフォンでの操作", "1本指でなぞる：カーソル移動\n\n1本指でタップ：左クリック（すばやく2回でダブルクリック）\n\n2本指でタップ：右クリック\n\n2本指で上下に動かす：スクロール\n\n押したまま動かす：ドラッグ\n\n左右スワイプ：前／次のページ\n\n上部の入力欄：PCへ文字を送信"),
            "window" => ("ウィンドウと終了", "ウィンドウ右上の「×」で閉じても、アプリは終了せず通知領域に残り、スマートフォンとの接続も続きます。\n\nアプリをもう一度表示するには、タスクバー右下の通知領域にあるPhone-Mouseアイコンをクリックします。\n\n完全に終了するときは、通知領域のPhone-Mouseアイコンを右クリックして「終了」を選んでください。"),
            _ => ("接続できないとき", "・PCとスマートフォンが同じWi-Fiに接続されているか確認してください。\n\n・QRコードを更新した場合は、新しいQRコードを読み取ってください。\n\n・スマートフォン側で前の接続タブを閉じてから、もう一度QRコードを読み取ってください。\n\n・Windowsのファイアウォールが通信を止めていないか確認してください。")
        };
        SetMenuOpen(false);
    }

    private void MenuToggle_Click(object sender, RoutedEventArgs e) => SetMenuOpen(!menuOpen);
    private void MenuScrim_MouseDown(object sender, System.Windows.Input.MouseButtonEventArgs e) => SetMenuOpen(false);
    private void SetMenuOpen(bool open)
    {
        if (menuOpen == open) return;
        menuOpen = open;
        var transform = (TranslateTransform)NavigationDrawer.RenderTransform;
        NavigationDrawer.IsHitTestVisible = open;
        if (open) { NavigationDrawer.Visibility = Visibility.Visible; MenuScrim.Visibility = Visibility.Visible; }
        var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
        transform.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(open ? 0 : -280, TimeSpan.FromMilliseconds(220)) { EasingFunction = ease });
        var scrimAnimation = new DoubleAnimation(open ? 0.42 : 0, TimeSpan.FromMilliseconds(220)) { EasingFunction = ease };
        scrimAnimation.Completed += (_, _) => { if (!open) { NavigationDrawer.Visibility = Visibility.Collapsed; MenuScrim.Visibility = Visibility.Collapsed; } };
        MenuScrim.BeginAnimation(OpacityProperty, scrimAnimation);
    }

    private void DarkTheme_Click(object sender, RoutedEventArgs e) => ApplyTheme(true, true);
    private void LightTheme_Click(object sender, RoutedEventArgs e) => ApplyTheme(false, true);
    private void ApplyTheme(bool dark, bool save)
    {
        darkTheme = dark;
        AppTitleBar.ApplicationTheme = dark ? ApplicationTheme.Dark : ApplicationTheme.Light;
        SetBrush("AppBackground", dark ? "#101318" : "#FFFFFF");
        SetBrush("SidebarBackground", dark ? "#151A21" : "#FFFFFF");
        SetBrush("PanelBackground", dark ? "#171D25" : "#FFFFFF");
        SetBrush("PanelBorder", dark ? "#2B3440" : "#D5DCE5");
        SetBrush("MainText", dark ? "#E9EDF4" : "#202833");
        SetBrush("SecondaryText", dark ? "#AAB4C2" : "#586575");
        SetBrush("ButtonSurface", dark ? "#252B34" : "#FFFFFF");
        SetBrush("ButtonHoverSurface", dark ? "#343D49" : "#EEF1F5");
        SetBrush("ButtonText", dark ? "#F5F7FA" : "#202833");
        SetBrush("AccentSurface", dark ? "#C98BE3" : "#B76CD1");
        SetBrush("AccentHoverSurface", dark ? "#D6A5EA" : "#A95DC5");
        SetBrush("AccentText", "#24152B");
        ThemeStatus.Text = dark ? "現在：ダーク" : "現在：ライト";
        DarkThemeButton.IsEnabled = !dark;
        LightThemeButton.IsEnabled = dark;
        if (save) try { Directory.CreateDirectory(Path.GetDirectoryName(ThemePath)!); File.WriteAllText(ThemePath, dark ? "dark" : "light"); } catch { }
    }
    private static void SetBrush(string key, string color) => System.Windows.Application.Current.Resources[key] = new SolidColorBrush((System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(color));
    private string ThemePath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhoneMouse", "ui-theme.txt");
    private void LoadThemePreference() { try { darkTheme = !File.Exists(ThemePath) || File.ReadAllText(ThemePath).Trim() != "light"; } catch { darkTheme = true; } }
    private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string RunValueName = "Phone-Mouse";
    private static bool IsAutoStartEnabled()
    {
        try { using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath); return key?.GetValue(RunValueName) is not null; }
        catch { return false; }
    }
    private void AutoStart_Changed(object sender, RoutedEventArgs e)
    {
        if (loadingStartupOption) return;
        var enabled = AutoStartCheck.IsChecked == true;
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath, true);
            if (key is null) throw new InvalidOperationException("自動起動の設定を保存できませんでした。");
            if (enabled)
            {
                var executable = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(executable)) throw new InvalidOperationException("アプリの場所を確認できませんでした。");
                key.SetValue(RunValueName, $"\"{executable}\"");
                AutoStartStatus.Text = "WindowsにサインインしたときにPhone-Mouseを起動します。";
            }
            else
            {
                key.DeleteValue(RunValueName, false);
                AutoStartStatus.Text = "PC起動時の自動起動はオフです。";
            }
        }
        catch (Exception ex)
        {
            loadingStartupOption = true;
            AutoStartCheck.IsChecked = !enabled;
            loadingStartupOption = false;
            AutoStartStatus.Text = $"設定を保存できませんでした: {ex.Message}";
        }
    }

    private string NotificationPreferencesPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhoneMouse", "notifications.json");
    private void LoadNotificationPreferences()
    {
        try
        {
            if (!File.Exists(NotificationPreferencesPath)) return;
            using var document = System.Text.Json.JsonDocument.Parse(File.ReadAllText(NotificationPreferencesPath));
            var root = document.RootElement;
            if (root.TryGetProperty("connection", out var connection)) notifyConnection = connection.GetBoolean();
            if (root.TryGetProperty("disconnect", out var disconnect)) notifyDisconnect = disconnect.GetBoolean();
            if (root.TryGetProperty("error", out var error)) notifyError = error.GetBoolean();
        }
        catch { }
    }

    private void NotificationPreference_Changed(object sender, RoutedEventArgs e)
    {
        if (loadingNotificationPreferences) return;
        notifyConnection = NotifyConnectionCheck.IsChecked == true;
        notifyDisconnect = NotifyDisconnectCheck.IsChecked == true;
        notifyError = NotifyErrorCheck.IsChecked == true;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(NotificationPreferencesPath)!);
            var preferences = new { connection = notifyConnection, disconnect = notifyDisconnect, error = notifyError };
            File.WriteAllText(NotificationPreferencesPath, System.Text.Json.JsonSerializer.Serialize(preferences));
        }
        catch (Exception ex)
        {
            if (notifyError) tray?.ShowBalloonTip(2500, "Phone-Mouse", $"通知設定を保存できませんでした: {ex.Message}", ToolTipIcon.Error);
        }
    }

    private void OnClosing(object? sender, System.ComponentModel.CancelEventArgs e) { if (!exiting) { e.Cancel = true; Hide(); } else { tray?.Dispose(); SystemEvents.DisplaySettingsChanged -= OnDisplayChanged; } }
    private void RestoreWindow() { if (exiting) return; Show(); WindowState = WindowState.Normal; Activate(); }
    private void OnDisplayChanged(object? sender, EventArgs e) { }
}
