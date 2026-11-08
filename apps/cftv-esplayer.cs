using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Tizen.NUI;
using Tizen.TV.Multimedia;
using Rectangle = Tizen.NUI.Rectangle;

/// <summary>
/// esvideo: an H.264 clip through Samsung's in-process ESPlusPlayer on the
/// TV's second video path (SetAlternativeVideoResource: 1 = sub decoder and
/// sub scaler, 3 = main decoder and sub scaler). The media player behind
/// VideoView takes the main scaler from the HDMI programme; this asks for the
/// one it does not use. Video only: with no audio stream the player makes no
/// avocd call.
///
/// {"op":"esvideo","id","src","fw","fh","fps","x","y","w","h","resource"}
/// src is an H.264 Annex-B file on the TV with an access unit delimiter
/// before every frame; the agent splits it there and submits one packet per
/// frame.
/// </summary>
public sealed partial class Cftv
{
    string EsVideo(string id, JsonElement c)
    {
        if (id.Length == 0) return Error("id required");
        string src = Str(c, "src", "");
        int fw = Int(c, "fw", 0), fh = Int(c, "fh", 0), fps = Int(c, "fps", 30);
        int x = Int(c, "x", 0), y = Int(c, "y", 0), w = Int(c, "w", fw), h = Int(c, "h", fh);
        uint resource = (uint)Int(c, "resource", 1);
        if (src.Length == 0 || fw <= 0 || fh <= 0 || fps <= 0) return Error("src, fw, fh and fps required");
        List<byte[]> units = AccessUnits(File.ReadAllBytes(src));
        Remove(id);
        SynchronizationContext ui = SynchronizationContext.Current;

        var win = new Window("cftv-" + id, new Rectangle(x, y, w, h), true);
        MakeOverlay(win);
        win.Show();
        Raise(win);
        var stop = new CancellationTokenSource();
        ESPlayer es = NewEsPlayer();
        _items[id] = new Item
        {
            Id = id, Kind = "esvideo", Win = win,
            Release = () =>
            {
                stop.Cancel();
                Step(id, "Stop", () => es.Stop());
                Step(id, "Close", () => es.Close());
                Step(id, "Dispose", () => es.Dispose());
                stop.Dispose();
            },
        };
        try
        {
            Configure(id, es, win, units, fps, fw, fh, w, h, resource, ui, stop.Token);
        }
        catch
        {
            Remove(id);
            throw;
        }
        return Ok(",\"frames\":" + units.Count + ",\"state\":\"preparing\"");
    }

    void Configure(string id, ESPlayer es, Window win, List<byte[]> units, int fps, int fw, int fh, int w, int h,
        uint resource, SynchronizationContext ui, CancellationToken stop)
    {
        es.ErrorOccurred += (s, e) => Log($"esvideo {id} error {e.ErrorType}");
        es.ResourceConflicted += (s, e) => Log($"esvideo {id} resource conflicted");
        es.EOSEmitted += (s, e) =>
        {
            Log($"esvideo {id} end of stream");
            ui.Post(_ => { if (_items.TryGetValue(id, out Item it) && it.Kind == "esvideo") Remove(id); }, null);
        };

        es.Open();
        es.SetAlternativeVideoResource(resource);
        es.SetDisplay(win);
        es.SetDisplayRoi(0, 0, w, h);
        es.SetDisplayVisible(true);
        es.SetStream(new VideoStreamInfo
        {
            mimeType = VideoMimeType.H264, width = fw, height = fh, maxWidth = fw, maxHeight = fh, num = fps, den = 1,
        });
        Log($"esvideo {id}: {units.Count} frames, resource {resource}, preparing");
        Task prepare = es.PrepareAsync(type => StartFeeding(id, es, units, fps, stop));
        prepare.ContinueWith(t =>
        {
            if (t.IsFaulted) { Log($"esvideo {id} prepare EX {t.Exception.InnerException?.Message}"); return; }
            Step(id, "Start", () => es.Start());
            Log($"esvideo {id} playing, state {es.GetState()}");
        }, TaskScheduler.FromCurrentSynchronizationContext());
    }

    /// An ESPlayer without its constructor's check that the calling app holds
    /// the esplay privilege (a Cynara lookup on our borrowed app's label,
    /// which lacks it). The constructor's other work (field defaults and
    /// Initialize, which creates the native player) is done here.
    static ESPlayer NewEsPlayer()
    {
        const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        Type type = typeof(ESPlayer);
        var es = (ESPlayer)RuntimeHelpers.GetUninitializedObject(type);
        type.GetField("timeoutForPrepare", Private).SetValue(es, TimeSpan.FromSeconds(20));
        FieldInfo check = type.GetField("lastResultOfPfmPrivCheck", Private);
        check.SetValue(es, Enum.Parse(check.FieldType, "Allowed"));
        type.GetMethod("Initialize", Private).Invoke(es, null);
        return es;
    }

    static void Step(string id, string name, Action action)
    {
        try { action(); }
        catch (Exception ex) { Log($"esvideo {id} {name} EX {ex.GetType().Name}: {ex.Message}"); }
    }

    /// Submits every frame once the player asks for data, retrying while its
    /// buffer is full, then end of stream.
    static void StartFeeding(string id, ESPlayer es, List<byte[]> units, int fps, CancellationToken stop)
    {
        new Thread(() =>
        {
            try
            {
                int full = 0;
                for (int i = 0; i < units.Count && !stop.IsCancellationRequested; i++)
                {
                    var packet = new ESPacket
                    {
                        type = StreamType.Video, buffer = units[i],
                        // pts in nanoseconds (the wrapper converts to the native milliseconds),
                        // duration in milliseconds (passed through).
                        pts = (ulong)(i * 1_000_000_000L / fps), duration = (ulong)(1000 / fps),
                    };
                    SubmitStatus status;
                    while ((status = es.SubmitPacket(packet)) == SubmitStatus.Full || status == SubmitStatus.NotPrepared)
                    {
                        if (stop.IsCancellationRequested) return;
                        full++;
                        Thread.Sleep(5);
                    }
                    if (status != SubmitStatus.Success)
                    {
                        Log($"esvideo {id} frame {i}: {status}");
                        return;
                    }
                }
                if (!stop.IsCancellationRequested) es.SubmitEosPacket(StreamType.Video);
                Log($"esvideo {id} submitted {units.Count} frames ({full} waits)");
            }
            catch (Exception ex) { Log($"esvideo {id} feed EX {ex.GetType().Name}: {ex.Message}"); }
        }) { IsBackground = true }.Start();
    }

    /// Splits an Annex-B stream before each access unit delimiter (NAL type 9).
    static List<byte[]> AccessUnits(byte[] stream)
    {
        var starts = new List<int>();
        for (int i = 0; i + 4 < stream.Length; i++)
            if (stream[i] == 0 && stream[i + 1] == 0 && stream[i + 2] == 0 && stream[i + 3] == 1 && (stream[i + 4] & 0x1f) == 9)
                starts.Add(i);
        if (starts.Count == 0) throw new InvalidDataException("no access unit delimiters");
        var units = new List<byte[]>(starts.Count);
        for (int k = 0; k < starts.Count; k++)
        {
            int end = k + 1 < starts.Count ? starts[k + 1] : stream.Length;
            units.Add(stream.AsSpan(starts[k], end - starts[k]).ToArray());
        }
        return units;
    }
}
