using System.Configuration;
using System.Data;
using System.Windows;

namespace PhoneMouse.Desktop;

/// <summary>
/// Interaction logic for App.xaml
/// </summary>
public partial class App : System.Windows.Application
{
    private const string SingleInstanceMutexName = @"Local\PhoneMouse.SingleInstance.2026";
    private System.Threading.Mutex? singleInstanceMutex;
    private bool ownsSingleInstanceMutex;

    protected override void OnStartup(StartupEventArgs e)
    {
        try
        {
            singleInstanceMutex = new System.Threading.Mutex(true, SingleInstanceMutexName, out var createdNew);
            if (!createdNew)
            {
                singleInstanceMutex.Dispose();
                singleInstanceMutex = null;
                System.Windows.MessageBox.Show(
                    "Phone-Mouseはすでに起動しています。2つ目の起動を終了します。\n右下の通知領域にあるアイコンから表示できます。",
                    "Phone-Mouseは起動中です",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Warning);
                Shutdown();
                return;
            }

            ownsSingleInstanceMutex = true;
            DispatcherUnhandledException += OnDispatcherUnhandledException;
            base.OnStartup(e);
            var window = new MainWindow();
            MainWindow = window;
            window.Show();
        }
        catch (Exception ex)
        {
            WriteStartupError(ex);
            System.Windows.MessageBox.Show(
                $"Phone-Mouseを起動できませんでした。\n{ex.Message}\n\n詳細を保存しました:\n{StartupErrorPath}",
                "Phone-Mouse 起動エラー",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
            Shutdown(-1);
        }
    }

    private void OnDispatcherUnhandledException(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
    {
        WriteStartupError(e.Exception);
        System.Windows.MessageBox.Show(
            $"Phone-Mouseでエラーが発生しました。\n{e.Exception.Message}\n\n詳細を保存しました:\n{StartupErrorPath}",
            "Phone-Mouse エラー",
            System.Windows.MessageBoxButton.OK,
            System.Windows.MessageBoxImage.Error);
        e.Handled = true;
    }

    private static string StartupErrorPath => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhoneMouse", "startup-error.log");

    private static void WriteStartupError(Exception exception)
    {
        try
        {
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(StartupErrorPath)!);
            System.IO.File.WriteAllText(StartupErrorPath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}\n{exception}");
        }
        catch { }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (ownsSingleInstanceMutex && singleInstanceMutex is not null)
            singleInstanceMutex.ReleaseMutex();
        singleInstanceMutex?.Dispose();
        singleInstanceMutex = null;
        base.OnExit(e);
    }
}

