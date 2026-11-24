using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using Timer = Tizen.NUI.Timer;
using Tizen.Multimedia;
using Tizen.NUI;
using Tizen.NUI.BaseComponents;
using Path = System.IO.Path;
using Color = Tizen.NUI.Color;
using Rectangle = Tizen.NUI.Rectangle;

/// <summary>
/// cftv - persistent on-screen agent. Shows what the host sends it: images,
/// animated images, text, rectangles, video files and streams, sounds, speech.
///
/// Runs in a borrowed app slot (tv/cftv.py SLOTS, uid 5001), whose entry DLL
/// it replaces.
/// The host (tv/cftv.py) connects to TCP port 7741, sends the token from
/// Q/token as its first line, then one JSON command per line; the agent
/// answers each with one JSON line. A reader thread per connection hands each
/// command to the UI thread and waits for its reply, so commands from one
/// connection run in order.
///
/// Rules, each learned on this TV (docs/03-video/transparent-overlay.md):
///   - Every item gets its own window sized to the item. (The HUD is one
///     full-screen transparent window, which keeps the HDMI picture; see
///     cftv-hud.cs.)
///   - Windows never accept focus and pass all input through, so the remote
///     keeps driving whatever is playing. The app never exits by itself: any
///     exit (Exit(), app_launcher -t or -k) hands focus to Smart Hub and its
///     bar slides up over the picture.
///   - Windows are re-raised once a second; newly launched apps stack above us.
///   - NUI timers must be referenced or they are collected and stop silently.
/// </summary>
public sealed partial class Cftv : NUIApplication
{
    // The slot's app id is the directory the launcher loaded this DLL from:
    // /opt/usr/apps/<app>/bin/<entry>.dll. The host (tv/cftv.py) picks the slot.
    static readonly string App = new DirectoryInfo(Path.GetDirectoryName(typeof(Cftv).Assembly.Location)).Parent.Name;
    static readonly string Q = "/opt/usr/home/owner/apps_rw/" + App + "/cftv/";
    static readonly string LogPath = Q + "cftv.log", TokenPath = Q + "token";
    const int Port = 7741;

    sealed class Item
    {
        public string Id, Kind;
        public Window Win;
        public View View;
        public CancellationTokenSource Sound; // cancels the item's sound
        public TTSPlayer Tts;
        public Action Release; // frees what the item holds besides its window
        // Notifications only (Notify): card height, leaving = exit animation
        // running, and the animation and timers, kept referenced so they are
        // not collected while running (§7 of transparent-overlay.md).
        public int Height;
        public bool Leaving;
        public Animation Anim;
        public Timer Dwell, Slide;
    }

    readonly Dictionary<string, Item> _items = new Dictionary<string, Item>();
    readonly List<Timer> _timers = new List<Timer>();
    // Notifications on screen, top to bottom.
    readonly List<Item> _stack = new List<Item>();

    [DllImport("libecore_wl2.so.1")] static extern void ecore_wl2_window_raise(IntPtr win);
    [DllImport("libc", SetLastError = true)] static extern int chmod(string path, uint mode);

    static void Main(string[] args)
    {
        Directory.CreateDirectory(Q);
        Log("start pid " + Pid);
        // The default window only exists because NUI needs one; it shrinks to
        // 1x1 once the launch screen is gone (OnCreate). It must be created in
        // Transparent mode with an explicit size: with the default constructor
        // no window reached the compositor and the launch screen never left.
        new Cftv("", WindowMode.Transparent, new Size2D(1920, 1080), new Position2D(0, 0)).Run(args);
    }

    Cftv() { }
    Cftv(string style, WindowMode mode, Size2D size, Position2D position) : base(style, mode, size, position) { }

    // Environment.ProcessId is .NET 5+; the TV runs .NET Core 3.1.
    static int Pid => System.Diagnostics.Process.GetCurrentProcess().Id;

    static void Log(string line)
    {
        try { File.AppendAllText(LogPath, DateTime.Now.ToString("HH:mm:ss.fff ") + line + "\n"); } catch { }
    }

    protected override void OnCreate()
    {
        base.OnCreate();
        Window main = GetDefaultWindow();
        MakeOverlay(main);
        // Tizen shows a launch screen until the app's window puts a frame on
        // screen, and that launch screen holds focus (and the remote) until it
        // is dismissed. NUI renders on demand, so an empty transparent scene
        // never commits a frame and the launch screen stays forever (observed
        // 2026-10-24). One nearly invisible pixel forces the first frame.
        main.Add(new View { Size2D = new Size2D(1, 1), BackgroundColor = new Color(0f, 0f, 0f, 0.01f) });
        Timer shrink = new Timer(500);
        shrink.Tick += (s, e) =>
        {
            main.WindowSize = new Size2D(1, 1);
            main.WindowPosition = new Position2D(0, 0);
            Log("main window shrunk");
            return false;
        };
        _timers.Add(shrink);
        shrink.Start();

        StartListener();

        Timer raise = new Timer(1000);
        raise.Tick += (s, e) =>
        {
            foreach (var it in _items.Values) Raise(it.Win);
            if (_hudOn) Raise(_hudWin);
            return true;
        };
        _timers.Add(raise);
        raise.Start();

        File.WriteAllText(Q + "ready", Pid.ToString());
        Log("ready");
    }

    static void MakeOverlay(Window w)
    {
        w.BackgroundColor = new Color(0f, 0f, 0f, 0f);
        try { w.SetAcceptFocus(false); } catch (Exception ex) { Log("focus EX " + ex.Message); }
        try { w.SetInputRegion(new Rectangle(0, 0, 0, 0)); } catch (Exception ex) { Log("region EX " + ex.Message); }
    }

    static void Raise(Window w)
    {
        if (w == null) return;
        try
        {
            var mi = w.GetType().GetMethod("GetNativeWindowHandler",
                BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
            if (mi != null) ecore_wl2_window_raise((IntPtr)mi.Invoke(w, null));
        }
        catch { }
    }

    // ------------------------------------------------------------ commands over TCP

    /// Listen on Port; only connections that send this run's token first are
    /// served. The token is new per agent start, readable by uid 5001 and root.
    void StartListener()
    {
        SynchronizationContext ui = SynchronizationContext.Current;
        byte[] secret = new byte[32];
        using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(secret);
        string token = string.Concat(secret.Select(b => b.ToString("x2")));
        File.WriteAllText(TokenPath, token);
        chmod(TokenPath, Convert.ToUInt32("600", 8));
        StartFramesListener(token);
        // Bind off the UI thread and retry: an exception in OnCreate would end
        // the app, and any exit hands focus to Smart Hub.
        new Thread(() =>
        {
            TcpListener listener = null;
            while (listener == null)
            {
                try
                {
                    listener = new TcpListener(IPAddress.Any, Port);
                    listener.Start();
                    Log("listening on " + Port);
                }
                catch (SocketException ex)
                {
                    Log("listen EX " + ex.Message + "; retrying");
                    listener = null;
                    Thread.Sleep(1000);
                }
            }
            while (true)
            {
                TcpClient client = listener.AcceptTcpClient();
                new Thread(() => Serve(client, ui, token)) { IsBackground = true }.Start();
            }
        }) { IsBackground = true }.Start();
    }

    void Serve(TcpClient client, SynchronizationContext ui, string token)
    {
        try
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            using (var reader = new StreamReader(stream, new UTF8Encoding(false)))
            using (var writer = new StreamWriter(stream, new UTF8Encoding(false)) { NewLine = "\n", AutoFlush = true })
            {
                client.NoDelay = true;
                string first = reader.ReadLine() ?? "";
                if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(first), Encoding.ASCII.GetBytes(token)))
                {
                    Log("rejected " + client.Client.RemoteEndPoint);
                    return;
                }
                using (var done = new ManualResetEventSlim())
                {
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        string reply = null;
                        done.Reset();
                        ui.Post(_ => { reply = Run(line); done.Set(); }, null);
                        done.Wait();
                        writer.WriteLine(reply);
                    }
                }
            }
        }
        catch (IOException) { }  // the host went away
        catch (Exception ex) { Log("serve EX " + ex.GetType().Name + ": " + ex.Message); }
    }

    /// One command on the UI thread. `hud` updates (several a second) are not
    /// logged: the log is on flash.
    string Run(string json)
    {
        string reply, op = "";
        try
        {
            using (JsonDocument doc = JsonDocument.Parse(json))
            {
                op = Str(doc.RootElement, "op", "");
                reply = Execute(doc.RootElement);
            }
        }
        catch (Exception ex)
        {
            reply = Error(ex.GetType().Name + ": " + ex.Message);
        }
        if (op != "hud" || !reply.StartsWith("{\"ok\":true")) Log(op + " -> " + reply);
        return reply;
    }

    static string Ok(string extra = "") => "{\"ok\":true" + extra + "}";
    static string Error(string message) => "{\"ok\":false,\"error\":" + JsonSerializer.Serialize(message) + "}";

    // Set by Remove() when a VideoView is released. The TV's own HDMI/tuner
    // pipeline gives up the hardware video path to our player and does not
    // take it back; the host restores it by re-selecting the input
    // (tv/cftv.py restore_source), so every reply says whether that is needed.
    bool _videoReleased;

    string Execute(JsonElement c)
    {
        _videoReleased = false;
        string reply = ExecuteOp(c);
        return _videoReleased && reply.EndsWith("}")
            ? reply.Substring(0, reply.Length - 1) + ",\"video_released\":true}"
            : reply;
    }

    string ExecuteOp(JsonElement c)
    {
        string op = Str(c, "op", "");
        string id = Str(c, "id", "");
        switch (op)
        {
            case "ping":
                return Ok(",\"items\":" + JsonSerializer.Serialize(_items.Keys.ToArray()));
            case "image": return Show(id, op, c, ImageItem(c));
            case "text": return Show(id, op, c, TextItem(c));
            case "rect": return Show(id, op, c, RectItem(c));
            case "video": return Show(id, op, c, VideoItem(c));
            case "esvideo": return EsVideo(id, c);
            case "sound": return Sound(id, c);
            case "say": return Say(id, c);
            case "notify": return Notify(id, c);
            case "hud": return Hud(c);
            case "move": return Move(id, c);
            case "remove":
                if (!_items.TryGetValue(id, out Item item)) return Error("no item " + id);
                if (item.Kind == "notify") Dismiss(item);
                else Remove(id);
                return Ok();
            case "clear":
                foreach (string key in _items.Keys.ToList()) Remove(key);
                HudOff();
                return Ok();
            // No "quit": any exit of this app hands focus to Smart Hub, whose
            // bar then slides up over the picture (screenshots 2026-11-05;
            // hiding the window before Exit() did not help). The agent stays
            // resident; `clear` removes everything.
            default:
                return Error("unknown op '" + op + "'");
        }
    }

    // ------------------------------------------------------------ visual items

    string Show(string id, string kind, JsonElement c, View view)
    {
        if (id.Length == 0) return Error("id required");
        Remove(id);
        int x = Int(c, "x", 0), y = Int(c, "y", 0);
        int w = Int(c, "w", 400), h = Int(c, "h", 300);
        var win = new Window("cftv-" + id, new Rectangle(x, y, w, h), true);
        MakeOverlay(win);
        view.Position2D = new Position2D(0, 0);
        view.Size2D = new Size2D(w, h);
        view.Opacity = Float(c, "opacity", 1f);
        win.Add(view);
        win.Show();
        Raise(win);
        _items[id] = new Item { Id = id, Kind = kind, Win = win, View = view };
        if (view is VideoView video) video.Play();
        if (view is AnimatedImageView anim) anim.Play();
        return Ok();
    }

    View ImageItem(JsonElement c)
    {
        string src = Str(c, "src", "");
        if (src.Length == 0) throw new ArgumentException("src required");
        string lower = src.ToLowerInvariant();
        ImageView view = lower.EndsWith(".gif") || lower.EndsWith(".webp")
            ? new AnimatedImageView { ResourceUrl = src, LoopCount = 0 }
            : new ImageView { ResourceUrl = src };
        // Alpha PNG/GIF/WebP frames carry straight alpha; premultiply to blend.
        view.PreMultipliedAlpha = true;
        return view;
    }

    View TextItem(JsonElement c)
    {
        return new TextLabel
        {
            Text = Str(c, "text", ""),
            PixelSize = Float(c, "size", 48f),
            TextColor = ParseColor(Str(c, "color", "#FFFFFFFF")),
            BackgroundColor = ParseColor(Str(c, "bg", "#00000000")),
            MultiLine = true,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
        };
    }

    View RectItem(JsonElement c)
    {
        return new View
        {
            BackgroundColor = ParseColor(Str(c, "color", "#FF0000FF")),
            CornerRadius = Float(c, "radius", 0f),
        };
    }

    View VideoItem(JsonElement c)
    {
        string src = Str(c, "src", "");
        if (src.Length == 0) throw new ArgumentException("src required");
        return new VideoView
        {
            ResourceUrl = src,
            Looping = Bool(c, "loop", false),
        };
    }

    string Move(string id, JsonElement c)
    {
        if (!_items.TryGetValue(id, out Item it) || it.Win == null) return Error("no visual item " + id);
        Rectangle r = it.Win.WindowPositionSize;
        int x = Int(c, "x", r.X), y = Int(c, "y", r.Y), w = Int(c, "w", r.Width), h = Int(c, "h", r.Height);
        it.Win.WindowPositionSize = new Rectangle(x, y, w, h);
        it.View.Size2D = new Size2D(w, h);
        if (c.TryGetProperty("opacity", out _)) it.View.Opacity = Float(c, "opacity", 1f);
        return Ok();
    }

    // ------------------------------------------------------------ audio items

    string Sound(string id, JsonElement c)
    {
        if (id.Length == 0) return Error("id required");
        string src = Str(c, "src", "");
        if (src.Length == 0) return Error("src required");
        Remove(id);
        _items[id] = new Item { Id = id, Kind = "sound", Sound = Play(id, src, Bool(c, "loop", false)) };
        return Ok(",\"state\":\"playing\"");
    }

    // Sounds mix into whatever the TV is playing, as its own UI sounds do. A
    // media Player would take the TV's main audio output from the HDMI source
    // (avocd PlayRequestM) and never hand it back: the programme stays silent
    // until the input changes. WavPlayer plays through the sound server alone.
    static CancellationTokenSource Play(string id, string path, bool loop)
    {
        var stop = new CancellationTokenSource();
        PlayUntilDone(id, path, loop, stop.Token);
        return stop;
    }

    static async void PlayUntilDone(string id, string path, bool loop, CancellationToken stop)
    {
        try
        {
            using (var policy = new AudioStreamPolicy(AudioStreamType.Notification))
            {
                do await WavPlayer.StartAsync(path, policy, stop);
                while (loop && !stop.IsCancellationRequested);
            }
            Log("sound " + id + " completed");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { Log("sound " + id + " EX " + ex.GetType().Name + ": " + ex.Message); }
    }

    string Say(string id, JsonElement c)
    {
        string text = Str(c, "text", "");
        if (text.Length == 0) return Error("text required");
        if (id.Length == 0) id = "say";
        Remove(id);
        TTSPlayer tts = TTSPlayer.Get(TTSPlayer.TTSMode.Default);
        if (tts == null) return Error("no TTS engine");
        tts.Play(text);
        _items[id] = new Item { Id = id, Kind = "say", Tts = tts };
        TTSPlayer.TTSState state = tts.GetState();
        // Cold-engine trap (observed 2026-10-23): the first
        // Play() after idle can leave the player Unavailable and say nothing.
        // Replay once, but only if it is still Unavailable 1.2 s later; on a
        // slow cold start the first Play() can still succeed, and replaying
        // then speaks the text twice.
        if (state == TTSPlayer.TTSState.Unavailable)
        {
            Timer retry = new Timer(1200);
            retry.Tick += (s, e) =>
            {
                try
                {
                    if (tts.GetState() == TTSPlayer.TTSState.Unavailable)
                    {
                        tts.Play(text);
                        Log("say " + id + " replayed after cold engine, state=" + tts.GetState());
                    }
                }
                catch (Exception ex) { Log("say retry EX " + ex.Message); }
                return false;
            };
            _timers.Add(retry);
            retry.Start();
        }
        return Ok(",\"state\":\"" + state + "\"");
    }

    // ------------------------------------------------------------ notifications
    //
    // A card in the top-right corner: optional icon, title and text, and an
    // optional sound. It slides in from the right edge, stays `duration`
    // seconds (0 = until `remove`), and slides out. Newest on top; the others
    // slide down to make room and back up when one leaves.
    //
    // Each card has its own window, reaching from the card's left edge to the
    // screen's right edge. The card slides inside it; the window clips it, so
    // it appears out of the screen edge. Windows themselves cannot be animated
    // by NUI, so stack moves step the window position with a 16 ms timer.

    const int ScreenW = 1920, Margin = 32, Gap = 16, Pad = 24, IconSize = 72;
    const int EnterMs = 450, LeaveMs = 350, ShiftMs = 250;

    string Notify(string id, JsonElement c)
    {
        if (id.Length == 0) return Error("id required");
        string title = Str(c, "title", ""), text = Str(c, "text", ""), icon = Str(c, "icon", "");
        if (title.Length == 0 && text.Length == 0) return Error("title or text required");
        Remove(id);

        string sound = Str(c, "sound", "");
        CancellationTokenSource stopSound = sound.Length > 0 ? Play(id, sound, false) : null;

        int w = Int(c, "w", 560);
        int textX = icon.Length > 0 ? Pad + IconSize + 20 : Pad;
        int textW = w - textX - Pad;
        var labels = new List<TextLabel>();
        if (title.Length > 0)
        {
            var bold = new PropertyMap();
            bold.Add("weight", new PropertyValue("bold"));
            labels.Add(Label(title, Float(c, "title_size", 34f), ParseColor(Str(c, "color", "#FFFFFFFF")), bold));
        }
        if (text.Length > 0)
            labels.Add(Label(text, Float(c, "size", 28f), ParseColor(Str(c, "text_color", "#FFFFFFCC")), null));

        // Text block height at the card's text width, then the card height:
        // at least the icon plus padding, text centred vertically.
        var heights = labels.Select(l => (int)Math.Ceiling(l.GetHeightForWidth(textW))).ToList();
        int block = heights.Sum() + 6 * (labels.Count - 1);
        int h = Math.Max(block, icon.Length > 0 ? IconSize : 0) + 2 * Pad;

        var card = new View
        {
            Size2D = new Size2D(w, h),
            BackgroundColor = ParseColor(Str(c, "bg", "#1C1C1EE6")),
            CornerRadius = 20f,
            Opacity = 0f,
        };
        if (icon.Length > 0)
            card.Add(new ImageView
            {
                ResourceUrl = icon,
                PreMultipliedAlpha = true,
                Size2D = new Size2D(IconSize, IconSize),
                Position2D = new Position2D(Pad, (h - IconSize) / 2),
            });
        int y = (h - block) / 2;
        for (int i = 0; i < labels.Count; i++)
        {
            labels[i].Size2D = new Size2D(textW, heights[i]);
            labels[i].Position2D = new Position2D(textX, y);
            card.Add(labels[i]);
            y += heights[i] + 6;
        }

        var win = new Window("cftv-" + id, new Rectangle(ScreenW - w - Margin, Margin, w + Margin, h), true);
        MakeOverlay(win);
        card.Position2D = new Position2D(w + Margin, 0);
        win.Add(card);
        win.Show();
        Raise(win);

        var it = new Item { Id = id, Kind = "notify", Win = win, View = card, Height = h, Sound = stopSound };
        _items[id] = it;
        _stack.Insert(0, it);
        Restack();

        it.Anim = new Animation(EnterMs);
        it.Anim.AnimateTo(card, "PositionX", 0f, new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseOutSine));
        it.Anim.AnimateTo(card, "Opacity", 1f, new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseOutSine));
        it.Anim.Play();

        float seconds = Float(c, "duration", 5f);
        if (seconds > 0)
        {
            it.Dwell = new Timer((uint)(seconds * 1000));
            it.Dwell.Tick += (s, e) => { Dismiss(it); return false; };
            it.Dwell.Start();
        }
        return Ok(",\"h\":" + h);
    }

    static TextLabel Label(string text, float size, Color color, PropertyMap style)
    {
        var label = new TextLabel
        {
            Text = text,
            PixelSize = size,
            TextColor = color,
            MultiLine = true,
            HorizontalAlignment = HorizontalAlignment.Begin,
        };
        if (style != null) label.FontStyle = style;
        return label;
    }

    /// Slide the card out to the right, then remove it (and close the gap).
    void Dismiss(Item it)
    {
        if (it.Leaving || !_items.TryGetValue(it.Id, out Item current) || current != it) return;
        it.Leaving = true;
        it.Dwell?.Stop();
        it.Anim?.Stop();
        it.Anim = new Animation(LeaveMs);
        it.Anim.AnimateTo(it.View, "PositionX", (float)(it.View.Size2D.Width + Margin),
            new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseInSine));
        it.Anim.AnimateTo(it.View, "Opacity", 0f, new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseInSine));
        it.Anim.Finished += (s, e) =>
        {
            if (_items.TryGetValue(it.Id, out Item still) && still == it) Remove(it.Id);
        };
        it.Anim.Play();
    }

    /// Move every card's window to its place in the stack.
    void Restack()
    {
        int y = Margin;
        foreach (Item it in _stack)
        {
            SlideTo(it, y);
            y += it.Height + Gap;
        }
    }

    void SlideTo(Item it, int target)
    {
        it.Slide?.Stop();
        Rectangle r = it.Win.WindowPositionSize;
        int from = r.Y;
        if (from == target) return;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        it.Slide = new Timer(16);
        it.Slide.Tick += (s, e) =>
        {
            double t = Math.Min(1.0, clock.ElapsedMilliseconds / (double)ShiftMs);
            double eased = 1 - Math.Pow(1 - t, 3);
            it.Win.WindowPosition = new Position2D(r.X, from + (int)Math.Round((target - from) * eased));
            return t < 1.0;
        };
        it.Slide.Start();
    }

    // ------------------------------------------------------------ removal

    bool Remove(string id)
    {
        if (!_items.TryGetValue(id, out Item it)) return false;
        _items.Remove(id);
        try { if (it.View is VideoView v) { v.Stop(); _videoReleased = true; } } catch { }
        try { if (it.Tts != null) it.Tts.Stop(); } catch { }
        it.Release?.Invoke();
        if (it.Sound != null)
        {
            it.Sound.Cancel();
            it.Sound.Dispose();
        }
        try { it.Dwell?.Stop(); it.Slide?.Stop(); it.Anim?.Stop(); } catch { }
        if (it.Win != null)
        {
            try { it.Win.Hide(); } catch { }
            try { it.Win.Dispose(); } catch { }
        }
        if (_stack.Remove(it)) Restack();
        return true;
    }

    // ------------------------------------------------------------ json helpers

    static string Str(JsonElement c, string key, string fallback) =>
        c.TryGetProperty(key, out JsonElement v) && v.ValueKind == JsonValueKind.String ? v.GetString() : fallback;

    static int Int(JsonElement c, string key, int fallback) =>
        c.TryGetProperty(key, out JsonElement v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : fallback;

    static float Float(JsonElement c, string key, float fallback) =>
        c.TryGetProperty(key, out JsonElement v) && v.ValueKind == JsonValueKind.Number ? (float)v.GetDouble() : fallback;

    static bool Bool(JsonElement c, string key, bool fallback) =>
        c.TryGetProperty(key, out JsonElement v) && (v.ValueKind == JsonValueKind.True || v.ValueKind == JsonValueKind.False)
            ? v.GetBoolean() : fallback;

    /// "#RRGGBB" or "#RRGGBBAA".
    static Color ParseColor(string hex)
    {
        hex = hex.TrimStart('#');
        if (hex.Length == 6) hex += "FF";
        if (hex.Length != 8) throw new ArgumentException("color must be #RRGGBB or #RRGGBBAA");
        float Part(int i) => Convert.ToInt32(hex.Substring(i, 2), 16) / 255f;
        return new Color(Part(0), Part(2), Part(4), Part(6));
    }
}
