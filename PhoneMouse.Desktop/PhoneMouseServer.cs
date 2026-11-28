using System.Net;
using System.IO;
using System;
using System.Linq;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using QRCoder;

namespace PhoneMouse.Desktop;

public sealed record ServerUpdate(string Url, byte[] Qr, bool Connected, string? Notice = null, bool IsError = false, int SignalBars = 0, bool PhonePageOpened = false, bool IsConnectionNotice = false, bool IsDisconnectNotice = false);

public sealed class PhoneMouseServer : IAsyncDisposable
{
    private const int Port = 8765;
    private const int GraceMs = 24 * 60 * 60 * 1000;
    private const uint HorizontalWheel = 0x1000;
    private const uint WmSysCommand = 0x0112;
    private const uint ScMonitorPower = 0xF170;
    private const int HwndBroadcast = 0xFFFF;
    private readonly object gate = new();
    private WebApplication? app;
    private WebSocket? active;
    private bool stopping;
    private CancellationTokenSource? grace;
    private bool sessionActive;
    private bool phonePageOpened;
    private string token = NewToken();
    private string url = "";
    private double sensitivity = 1.3, scrollSpeed = 1.0, remX, remY;
    private bool invertScroll;
    private int signalBars;
    private readonly string settingsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhoneMouse", "settings.json");
    public event Action<ServerUpdate>? Updated;
    public ServerUpdate Current => new(url, MakeQr(url), active?.State == WebSocketState.Open, SignalBars: signalBars, PhonePageOpened: phonePageOpened);

    public async Task StartAsync()
    {
        LoadSettings();
        var localIp = GetLocalIp();
        url = $"http://{localIp}:{Port}/?token={token}";
        if (!IPAddress.TryParse(localIp, out var localAddress) || !IsUsableLanAddress(localAddress))
            throw new InvalidOperationException("PCがWi-FiまたはLANに接続されていません。PCをネットワークに接続してからPhone-Mouseを再起動してください。");
        EnsurePortAvailable();
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseUrls($"http://0.0.0.0:{Port}");
        builder.Services.AddRouting();
        app = builder.Build();
        app.UseWebSockets();
        app.MapGet("/", async context =>
        {
            context.Response.Headers.CacheControl = "no-store, no-cache, must-revalidate, max-age=0";
            if (context.Request.Query["token"] == token && !phonePageOpened)
            {
                phonePageOpened = true;
                Publish();
            }
            await using var stream = typeof(PhoneMouseServer).Assembly.GetManifestResourceStream("PhoneMouse.Desktop.index.html");
            if (stream is null) { context.Response.StatusCode = 500; return; }
            context.Response.ContentType = "text/html; charset=utf-8";
            await stream.CopyToAsync(context.Response.Body);
        });
        app.MapGet("/favicon.ico", context => WriteEmbeddedIconAsync(context, "PhoneMouse.Desktop.assets.favicon.ico", "image/vnd.microsoft.icon"));
        app.MapGet("/apple-touch-icon.png", context => WriteEmbeddedIconAsync(context, "PhoneMouse.Desktop.assets.apple-touch-icon.png", "image/png"));
        app.MapGet("/check", context =>
        {
            context.Response.Headers.CacheControl = "no-store";
            context.Response.StatusCode = context.Request.Query["token"] == token ? StatusCodes.Status204NoContent : StatusCodes.Status410Gone;
            return Task.CompletedTask;
        });
        app.Map("/ws", HandleSocketAsync);
        await app.StartAsync();
        Publish(null);
    }

    private static void EnsurePortAvailable()
    {
        var probe = new TcpListener(IPAddress.Any, Port);
        try { probe.Start(); }
        catch (SocketException ex) when (ex.SocketErrorCode == SocketError.AddressAlreadyInUse)
        {
            throw new InvalidOperationException($"接続用ポート {Port} が別のアプリで使用中です。通知領域のPhone-Mouseがすでに起動していないか確認してください。", ex);
        }
        finally { probe.Stop(); }
    }

    private static async Task WriteEmbeddedIconAsync(HttpContext context, string resourceName, string contentType)
    {
        await using var stream = typeof(PhoneMouseServer).Assembly.GetManifestResourceStream(resourceName);
        if (stream is null) { context.Response.StatusCode = 404; return; }
        context.Response.ContentType = contentType;
        context.Response.Headers.CacheControl = "public, max-age=86400";
        await stream.CopyToAsync(context.Response.Body);
    }

    public async Task RefreshAsync()
    {
        token = NewToken(); url = $"http://{GetLocalIp()}:{Port}/?token={token}";
        WebSocket? socket;
        lock (gate)
        {
            socket = active;
            sessionActive = false;
            signalBars = 0;
            phonePageOpened = false;
            grace?.Cancel(); grace?.Dispose(); grace = null;
        }
        if (socket?.State == WebSocketState.Open)
            try { await socket.CloseAsync((WebSocketCloseStatus)4002, "QRコードが更新されました", CancellationToken.None); } catch { }
        Publish("QRコードを更新しました");
    }

    private async Task HandleSocketAsync(HttpContext ctx)
    {
        if (!ctx.WebSockets.IsWebSocketRequest) { ctx.Response.StatusCode = 400; return; }
        if (ctx.Request.Query["token"] != token) { ctx.Response.StatusCode = 403; return; }
        var socket = await ctx.WebSockets.AcceptWebSocketAsync();
        bool newSession;
        lock (gate)
        {
            if (stopping)
            {
                socket.Abort();
                socket.Dispose();
                return;
            }
            if (active?.State == WebSocketState.Open)
            {
                _ = socket.CloseAsync((WebSocketCloseStatus)4005, "別のタブが接続中です", CancellationToken.None);
                return;
            }
            newSession = !sessionActive;
            sessionActive = true;
            active = socket; signalBars = 0; grace?.Cancel(); grace?.Dispose(); grace = null;
        }
        Publish(newSession ? "接続完了" : null, true, connectionNotice: newSession);
        try
        {
            await SendJson(socket, new { type = "init_settings", settings = new { sensitivity, scroll_speed = scrollSpeed, invert_scroll = invertScroll } }, ctx.RequestAborted);
            var buffer = new byte[8192];
            while (socket.State == WebSocketState.Open)
            {
                using var ms = new MemoryStream(); WebSocketReceiveResult result;
                do { result = await socket.ReceiveAsync(buffer, ctx.RequestAborted); if (result.MessageType == WebSocketMessageType.Close) break; ms.Write(buffer, 0, result.Count); }
                while (!result.EndOfMessage);
                if (result.MessageType == WebSocketMessageType.Close) break;
                using var doc = JsonDocument.Parse(ms.ToArray());
                if (GetString(doc.RootElement, "type") == "ping")
                    await SendJson(socket, new { type = "pong", id = GetString(doc.RootElement, "id") }, ctx.RequestAborted);
                else
                    Process(doc.RootElement);
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
        catch (IOException) { }
        catch (Exception) { }
        finally
        {
            bool wasCurrent; lock (gate) { wasCurrent = ReferenceEquals(active, socket); if (wasCurrent) active = null; }
            if (wasCurrent)
            {
                signalBars = 0;
                var code = (int?)socket.CloseStatus ?? 1006;
                if (code == 4002)
                {
                    lock (gate) sessionActive = false;
                    Publish(null, false);
                }
                else
                {
                    // Mobile browsers can pause or close the socket when sent to the background.
                    // Keep this as a one-day grace session and avoid repeated balloon notifications.
                    // An explicit pagehide (4004) means the user closed/navigated away from the
                    // control tab, so show disconnected while retaining the grace session.
                    Publish(null, code != 4004);
                    lock (gate)
                    {
                        if (!stopping)
                        {
                            grace?.Cancel();
                            grace?.Dispose();
                            grace = new();
                            _ = GraceDisconnectAsync(grace);
                        }
                    }
                }
            }
            socket.Dispose();
        }
    }

    private async Task GraceDisconnectAsync(CancellationTokenSource? cts)
    {
        if (cts is null) return;
        try
        {
            await Task.Delay(GraceMs, cts.Token);
            lock (gate)
            {
                if (active is not null) return;
                sessionActive = false;
                phonePageOpened = false;
                if (ReferenceEquals(grace, cts)) { grace.Dispose(); grace = null; }
            }
            Publish("24時間再接続がなかったため接続を解除しました", false, disconnectNotice: true);
        }
        catch (OperationCanceledException) { }
    }

    private void Process(JsonElement m)
    {
        var type = GetString(m, "type"); double num(string key, double fallback = 0) => m.TryGetProperty(key, out var v) && v.TryGetDouble(out var n) ? n : fallback;
        switch (type)
        {
            case "move": remX += num("dx") * sensitivity; remY += num("dy") * sensitivity; int x=(int)remX,y=(int)remY; remX-=x;remY-=y;if(x!=0||y!=0) mouse_event(1, x, y, 0, 0); break;
            case "click": Click(false); break; case "double_click": Click(false); Click(false); break; case "right_click": Click(true); break;
            case "mouse_down": mouse_event(2,0,0,0,0); break; case "mouse_up": mouse_event(4,0,0,0,0); break;
            case "scroll": int wheel=(int)(num("dy")*scrollSpeed*4*(invertScroll?-1:1)); if(wheel!=0) mouse_event(0x800,0,0,wheel,0); break;
            case "scroll_horizontal":
                var horizontalWheel = (int)(num("dx") * scrollSpeed * 4 * (invertScroll ? -1 : 1));
                if (horizontalWheel != 0) mouse_event(HorizontalWheel, 0, 0, horizontalWheel, 0);
                break;
            case "back": Press(0xA6); break; case "forward": Press(0xA7); break;
            case "key_press": var key=GetString(m,"key"); var vk=key switch {"enter"=>0x0D,"backspace"=>0x08,"escape"=>0x1B,"tab"=>0x09,"space"=>0x20,_=>0}; if(vk!=0) Press(vk); break;
            case "select_all": PressControlShortcut(0x41); break;
            case "lock_pc": LockWorkStation(); break;
            case "display_off": SetDisplayPower(false); break;
            case "display_on": SetDisplayPower(true); break;
            case "copy": PressControlShortcut(0x43); break;
            case "type_text": var text=GetString(m,"text"); if(text.Length>0) System.Windows.Application.Current.Dispatcher.BeginInvoke(() => { System.Windows.Clipboard.SetText(text); PressControlShortcut(0x56); }); break;
            case "preview_settings": sensitivity=num("sensitivity",sensitivity);scrollSpeed=num("scroll_speed",scrollSpeed);if(m.TryGetProperty("invert_scroll",out var inv))invertScroll=inv.GetBoolean();break;
            case "save_settings": SaveSettings();break;
            case "quality":
                var bars = Math.Clamp((int)num("bars"), 0, 3);
                if (bars != signalBars) { signalBars = bars; Publish(); }
                break;
        }
    }

    private static string GetString(JsonElement m,string key)=>m.TryGetProperty(key,out var x)?x.GetString()??"":"";
    private static async Task SendJson(WebSocket ws, object value, CancellationToken ct)=>await ws.SendAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value)),WebSocketMessageType.Text,true,ct);
    private static void Click(bool right){mouse_event((uint)(right?8:2),0,0,0,0);mouse_event((uint)(right?16:4),0,0,0,0);}
    private static void Press(int vk){keybd_event((byte)vk,0,0,UIntPtr.Zero);keybd_event((byte)vk,0,2,UIntPtr.Zero);}
    private static void PressControlShortcut(int vk)
    {
        keybd_event(0x11, 0, 0, UIntPtr.Zero);
        Press(vk);
        keybd_event(0x11, 0, 2, UIntPtr.Zero);
    }
    private static string NewToken()=>Convert.ToBase64String(System.Security.Cryptography.RandomNumberGenerator.GetBytes(12)).TrimEnd('=').Replace('+','-').Replace('/','_');
    private static string GetLocalIp()
    {
        // Ask Windows which local address it would use for an outbound route.
        // UDP Connect only selects the route; it does not send a packet.
        try
        {
            using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            socket.Connect(IPAddress.Parse("8.8.8.8"), 80);
            if (socket.LocalEndPoint is IPEndPoint endpoint && IsUsableLanAddress(endpoint.Address))
                return endpoint.Address.ToString();
        }
        catch { }

        // Offline fallback: prefer a real Wi-Fi/Ethernet adapter with a gateway.
        try
        {
            var candidates = NetworkInterface.GetAllNetworkInterfaces()
                .Where(adapter => adapter.OperationalStatus == OperationalStatus.Up
                    && adapter.NetworkInterfaceType is NetworkInterfaceType.Wireless80211 or NetworkInterfaceType.Ethernet)
                .SelectMany(adapter =>
                {
                    var properties = adapter.GetIPProperties();
                    var hasGateway = properties.GatewayAddresses.Any(gateway =>
                        gateway.Address.AddressFamily == AddressFamily.InterNetwork
                        && !gateway.Address.Equals(IPAddress.Any));
                    var priority = adapter.NetworkInterfaceType == NetworkInterfaceType.Wireless80211 ? 0 : 1;
                    return properties.UnicastAddresses
                        .Where(address => IsUsableLanAddress(address.Address))
                        .Select(address => (Address: address.Address, HasGateway: hasGateway, Priority: priority));
                })
                .OrderByDescending(candidate => candidate.HasGateway)
                .ThenBy(candidate => candidate.Priority)
                .FirstOrDefault();
            if (candidates.Address is not null)
                return candidates.Address.ToString();
        }
        catch { }
        return "127.0.0.1";
    }

    private static bool IsUsableLanAddress(IPAddress address)
    {
        if (address.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(address))
            return false;
        var octets = address.GetAddressBytes();
        return octets[0] != 0 && !(octets[0] == 169 && octets[1] == 254);
    }
    private static byte[] MakeQr(string value){if(string.IsNullOrEmpty(value))return [];using var qr=new QRCodeGenerator().CreateQrCode(value,QRCodeGenerator.ECCLevel.Q);return new PngByteQRCode(qr).GetGraphic(8);}
    private void Publish(string? notice=null,bool? connected=null,bool error=false,bool connectionNotice=false,bool disconnectNotice=false)=>Updated?.Invoke(new(url,MakeQr(url),connected??(active?.State==WebSocketState.Open),notice,error,signalBars,phonePageOpened,connectionNotice,disconnectNotice));
    private void LoadSettings(){try{if(!File.Exists(settingsPath))return;using var d=JsonDocument.Parse(File.ReadAllText(settingsPath));var r=d.RootElement;if(r.TryGetProperty("sensitivity",out var a))sensitivity=a.GetDouble();if(r.TryGetProperty("scroll_speed",out var b))scrollSpeed=b.GetDouble();if(r.TryGetProperty("invert_scroll",out var c))invertScroll=c.GetBoolean();}catch{}}
    private void SaveSettings(){try{Directory.CreateDirectory(Path.GetDirectoryName(settingsPath)!);File.WriteAllText(settingsPath,JsonSerializer.Serialize(new{sensitivity,scroll_speed=scrollSpeed,invert_scroll=invertScroll},new JsonSerializerOptions{WriteIndented=true}));}catch{}}
    public async ValueTask DisposeAsync()
    {
        WebSocket? socket;
        lock (gate)
        {
            if (stopping) return;
            stopping = true;
            socket = active;
            active = null;
            grace?.Cancel();
            grace?.Dispose();
            grace = null;
        }
        socket?.Abort();
        if (app is not null)
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            try { await app.StopAsync(timeout.Token); }
            finally { await app.DisposeAsync(); }
        }
    }
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags,int dx,int dy,int data,nint extra);
    [DllImport("user32.dll")] private static extern void keybd_event(byte key,byte scan,uint flags,UIntPtr extra);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool LockWorkStation();

    private static void SetDisplayPower(bool on) =>
        PostMessageW(HwndBroadcast, WmSysCommand, ScMonitorPower, on ? -1 : 2);

    [DllImport("user32.dll", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessageW(nint window, uint message, nuint command, nint value);
}
