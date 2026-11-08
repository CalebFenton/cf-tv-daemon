using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using Tizen.NUI;
using Tizen.NUI.BaseComponents;

/// <summary>
/// Frames: video decoded on the host, drawn by the TV's GPU in our own
/// window. The TV's media player takes the main picture and audio away from
/// the HDMI input; this path never touches the player.
///
/// The host connects to FramesPort, sends the token line, then one JSON line
/// {"id","x","y","w","h","fw","fh","audio_bytes"}, then packets: a kind byte,
/// a 4-byte little-endian length, the payload.
///   'V': a JPEG of fw x fh with 4:2:0 chroma. libturbojpeg decodes it into
///        Y, U and V planes on the connection's thread; the planes become
///        three L8 textures and a shader converts them to RGB.
///   'A': audio_bytes of 48 kHz stereo S16LE PCM, played by AudioPlayback
///        through PulseAudio, mixed with the programme's sound.
/// When the host shuts down its side, the agent answers with one JSON line of
/// counters and removes the item.
/// </summary>
public sealed partial class Cftv
{
    const int FramesPort = 7743;

    const string FramesVertex = @"
attribute mediump vec2 aPosition;
uniform mediump mat4 uMvpMatrix;
uniform mediump vec3 uSize;
varying mediump vec2 vTexCoord;
void main()
{
    vTexCoord = aPosition + vec2(0.5);
    gl_Position = uMvpMatrix * vec4(aPosition * uSize.xy, 0.0, 1.0);
}";

    // JPEG's YCbCr: BT.601, full range.
    const string FramesFragment = @"
uniform sampler2D sY;
uniform sampler2D sU;
uniform sampler2D sV;
uniform lowp vec4 uColor;
varying mediump vec2 vTexCoord;
void main()
{
    mediump float y = texture2D(sY, vTexCoord).r;
    mediump float u = texture2D(sU, vTexCoord).r - 0.5;
    mediump float v = texture2D(sV, vTexCoord).r - 0.5;
    gl_FragColor = vec4(y + 1.402 * v, y - 0.344 * u - 0.714 * v, y + 1.772 * u, 1.0) * uColor;
}";

    sealed class FrameStream
    {
        public string Id;
        public int W, H;
        public View View;
        public Texture Y, U, V;
        public readonly object Lock = new object();
        // Three frames rotate between the decoder (filling one), Pending
        // (newest complete) and the UI thread.
        public readonly Stack<Frame> Free = new Stack<Frame>();
        public Frame Pending;
        public bool Posted;
        public int AudioBytes;
        public long Received, Shown, Dropped, Bytes, DecodeTicks, UploadTicks;
    }

    /// Plays fixed-size PCM packets on its own thread: AudioPlayback.Write
    /// blocks while PulseAudio's buffer is full, and must not stall video.
    sealed class PcmSink : IDisposable
    {
        const int Packets = 16;
        readonly Tizen.Multimedia.AudioPlayback _playback;
        readonly BlockingCollection<byte[]> _queue = new BlockingCollection<byte[]>(Packets);
        readonly BlockingCollection<byte[]> _free = new BlockingCollection<byte[]>(Packets);
        readonly Thread _thread;
        public long Played, Dropped;

        public PcmSink(int packetBytes)
        {
            _playback = new Tizen.Multimedia.AudioPlayback(48000, Tizen.Multimedia.AudioChannel.Stereo, Tizen.Multimedia.AudioSampleType.S16Le);
            _playback.Prepare();
            for (int i = 0; i < Packets; i++) _free.Add(new byte[packetBytes]);
            _thread = new Thread(() =>
            {
                foreach (byte[] packet in _queue.GetConsumingEnumerable())
                {
                    _playback.Write(packet);
                    Interlocked.Increment(ref Played);
                    _free.Add(packet);
                }
            }) { IsBackground = true };
            _thread.Start();
        }

        /// A free packet buffer, or null when all are queued (the packet is dropped).
        public byte[] Rent()
        {
            if (_free.TryTake(out byte[] packet)) return packet;
            Dropped++;
            return null;
        }

        public void Play(byte[] packet) => _queue.Add(packet);

        public void Dispose()
        {
            _queue.CompleteAdding();
            _thread.Join(2000);
            _playback.Unprepare();
            _playback.Dispose();
        }
    }

    /// Y, U, V planes, pinned while the stream lasts so libturbojpeg writes
    /// straight into them.
    sealed class Frame : IDisposable
    {
        public readonly byte[][] Planes;
        public readonly IntPtr[] Pointers = new IntPtr[3];
        readonly GCHandle[] _pins = new GCHandle[3];

        public Frame(int w, int h)
        {
            Planes = new[] { new byte[w * h], new byte[w * h / 4], new byte[w * h / 4] };
            for (int i = 0; i < 3; i++)
            {
                _pins[i] = GCHandle.Alloc(Planes[i], GCHandleType.Pinned);
                Pointers[i] = _pins[i].AddrOfPinnedObject();
            }
        }

        public void Dispose()
        {
            foreach (GCHandle pin in _pins) pin.Free();
        }
    }

    // libturbojpeg 2.x; `unsigned long` is pointer-sized on the TV (32-bit ARM).
    const int TjSamp420 = 2;
    [DllImport("libturbojpeg.so.0")] static extern IntPtr tjInitDecompress();
    [DllImport("libturbojpeg.so.0")] static extern int tjDestroy(IntPtr handle);
    [DllImport("libturbojpeg.so.0")] static extern IntPtr tjGetErrorStr2(IntPtr handle);
    [DllImport("libturbojpeg.so.0")]
    static extern int tjDecompressHeader3(IntPtr handle, byte[] jpeg, UIntPtr size,
        out int width, out int height, out int subsamp, out int colorspace);
    [DllImport("libturbojpeg.so.0")]
    static extern int tjDecompressToYUVPlanes(IntPtr handle, byte[] jpeg, UIntPtr size,
        IntPtr[] planes, int width, int[] strides, int height, int flags);

    static void Decode(IntPtr tj, byte[] jpeg, int size, FrameStream fs, Frame frame)
    {
        var length = (UIntPtr)(uint)size;
        if (tjDecompressHeader3(tj, jpeg, length, out int w, out int h, out int subsamp, out _) != 0)
            throw new InvalidDataException("JPEG header: " + Marshal.PtrToStringAnsi(tjGetErrorStr2(tj)));
        if (w != fs.W || h != fs.H || subsamp != TjSamp420)
            throw new InvalidDataException($"JPEG is {w}x{h} subsampling {subsamp}; the stream is {fs.W}x{fs.H} 4:2:0");
        if (tjDecompressToYUVPlanes(tj, jpeg, length, frame.Pointers, w, null, h, 0) != 0)
            throw new InvalidDataException("JPEG decode: " + Marshal.PtrToStringAnsi(tjGetErrorStr2(tj)));
    }

    void StartFramesListener(string token)
    {
        SynchronizationContext ui = SynchronizationContext.Current;
        new Thread(() =>
        {
            TcpListener listener = null;
            while (listener == null)
            {
                try
                {
                    listener = new TcpListener(IPAddress.Any, FramesPort);
                    listener.Start();
                }
                catch (SocketException ex)
                {
                    Log("frames listen EX " + ex.Message + "; retrying");
                    listener = null;
                    Thread.Sleep(1000);
                }
            }
            while (true)
            {
                TcpClient client = listener.AcceptTcpClient();
                new Thread(() => ServeFrames(client, ui, token)) { IsBackground = true }.Start();
            }
        }) { IsBackground = true }.Start();
    }

    void ServeFrames(TcpClient client, SynchronizationContext ui, string token)
    {
        FrameStream fs = null;
        var frames = new List<Frame>();
        IntPtr tj = IntPtr.Zero;
        PcmSink audio = null;
        try
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                client.ReceiveBufferSize = 4 << 20;
                if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(ReadLine(stream)), Encoding.ASCII.GetBytes(token)))
                {
                    Log("frames rejected " + client.Client.RemoteEndPoint);
                    return;
                }
                string header = ReadLine(stream);
                string error = null;
                using (var ready = new ManualResetEventSlim())
                {
                    ui.Post(_ =>
                    {
                        try { fs = OpenFrames(header); }
                        catch (Exception ex) { error = ex.GetType().Name + ": " + ex.Message; }
                        ready.Set();
                    }, null);
                    ready.Wait();
                }
                if (fs == null)
                {
                    Reply(stream, Error(error));
                    return;
                }
                for (int i = 0; i < 3; i++) frames.Add(new Frame(fs.W, fs.H));
                foreach (Frame f in frames) fs.Free.Push(f);
                tj = tjInitDecompress();
                if (fs.AudioBytes > 0) audio = new PcmSink(fs.AudioBytes);
                byte[] prefix = new byte[5], jpeg = new byte[1 << 20];
                var clock = System.Diagnostics.Stopwatch.StartNew();
                while (ReadFull(stream, prefix, 5))
                {
                    int size = BitConverter.ToInt32(prefix, 1);
                    if (size <= 0 || size > 16 << 20) throw new InvalidDataException("packet length " + size);
                    if (prefix[0] == 'A')
                    {
                        if (audio == null || size != fs.AudioBytes) throw new InvalidDataException($"audio packet of {size} bytes; the header said {fs.AudioBytes}");
                        byte[] pcm = audio.Rent();
                        if (!ReadFull(stream, pcm ?? jpeg, size)) throw new IOException("partial packet");
                        if (pcm != null) audio.Play(pcm);
                        continue;
                    }
                    if (prefix[0] != 'V') throw new InvalidDataException("packet kind " + prefix[0]);
                    if (size > jpeg.Length) jpeg = new byte[size];
                    if (!ReadFull(stream, jpeg, size)) throw new IOException("partial frame");
                    Frame buf;
                    lock (fs.Lock)
                    {
                        if (fs.Free.Count > 0) buf = fs.Free.Pop();
                        else { buf = fs.Pending; fs.Pending = null; fs.Dropped++; }
                    }
                    long start = System.Diagnostics.Stopwatch.GetTimestamp();
                    Decode(tj, jpeg, size, fs, buf);
                    long decoded = System.Diagnostics.Stopwatch.GetTimestamp() - start;
                    lock (fs.Lock)
                    {
                        fs.Received++;
                        fs.Bytes += size;
                        fs.DecodeTicks += decoded;
                        if (fs.Pending != null) { fs.Free.Push(fs.Pending); fs.Dropped++; }
                        fs.Pending = buf;
                        if (!fs.Posted)
                        {
                            fs.Posted = true;
                            ui.Post(_ => ShowFrame(fs), null);
                        }
                    }
                }
                double seconds = clock.Elapsed.TotalSeconds;
                Thread.Sleep(100);  // let the last posted frame show
                double ms = 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                string stats;
                lock (fs.Lock)
                    stats = Ok($",\"seconds\":{seconds:F2},\"received\":{fs.Received},\"shown\":{fs.Shown},\"dropped\":{fs.Dropped}" +
                               $",\"kbytes_per_frame\":{fs.Bytes / 1024.0 / Math.Max(fs.Received, 1):F1}" +
                               $",\"decode_ms\":{fs.DecodeTicks * ms / Math.Max(fs.Received, 1):F2}" +
                               $",\"upload_ms\":{fs.UploadTicks * ms / Math.Max(fs.Shown, 1):F2}" +
                               (audio == null ? "" : $",\"audio_played\":{audio.Played},\"audio_dropped\":{audio.Dropped}"));
                Log("frames " + fs.Id + " -> " + stats);
                Reply(stream, stats);
            }
        }
        catch (IOException) { }
        catch (Exception ex) { Log("frames EX " + ex.GetType().Name + ": " + ex.Message); }
        finally
        {
            if (tj != IntPtr.Zero) tjDestroy(tj);
            audio?.Dispose();
            foreach (Frame f in frames) f.Dispose();
            if (fs != null)
                ui.Post(_ => { if (_items.TryGetValue(fs.Id, out Item it) && it.View == fs.View) Remove(fs.Id); }, null);
        }
    }

    FrameStream OpenFrames(string header)
    {
        using (JsonDocument doc = JsonDocument.Parse(header))
        {
            JsonElement c = doc.RootElement;
            int fw = Int(c, "fw", 0), fh = Int(c, "fh", 0);
            if (fw <= 0 || fh <= 0 || fw % 2 != 0 || fh % 2 != 0) throw new ArgumentException("fw and fh must be positive and even");
            string id = Str(c, "id", "frames");
            var fs = new FrameStream
            {
                Id = id, W = fw, H = fh, AudioBytes = Int(c, "audio_bytes", 0),
                Y = new Texture(TextureType.TEXTURE_2D, PixelFormat.L8, (uint)fw, (uint)fh),
                U = new Texture(TextureType.TEXTURE_2D, PixelFormat.L8, (uint)fw / 2, (uint)fh / 2),
                V = new Texture(TextureType.TEXTURE_2D, PixelFormat.L8, (uint)fw / 2, (uint)fh / 2),
            };
            // DALi's default sampler keeps GL's default minification filter,
            // which needs mipmaps; without them the textures sample as 0.
            var sampler = new Sampler();
            sampler.SetFilterMode(FilterModeType.LINEAR, FilterModeType.LINEAR);
            sampler.SetWrapMode(WrapModeType.ClampToEdge, WrapModeType.ClampToEdge);
            var textures = new TextureSet();
            uint unit = 0;
            foreach (Texture t in new[] { fs.Y, fs.U, fs.V })
            {
                textures.SetTexture(unit, t);
                textures.SetSampler(unit++, sampler);
            }
            var renderer = new Renderer(QuadGeometry(), new Shader(FramesVertex, FramesFragment));
            renderer.SetTextures(textures);
            fs.View = new View();
            fs.View.AddRenderer(renderer);
            string reply = Show(id, "frames", c, fs.View);
            if (!reply.StartsWith("{\"ok\":true")) throw new InvalidOperationException(reply);
            return fs;
        }
    }

    static Geometry QuadGeometry()
    {
        var format = new PropertyMap();
        format.Add("aPosition", new PropertyValue((int)PropertyType.Vector2));
        var vertices = new PropertyBuffer(format);
        float[] quad = { -0.5f, -0.5f, 0.5f, -0.5f, -0.5f, 0.5f, 0.5f, 0.5f };
        GCHandle pin = GCHandle.Alloc(quad, GCHandleType.Pinned);
        try { vertices.SetData(pin.AddrOfPinnedObject(), 4); }
        finally { pin.Free(); }
        var geometry = new Geometry();
        geometry.AddVertexBuffer(vertices);
        geometry.SetType(Geometry.Type.TRIANGLE_STRIP);
        return geometry;
    }

    void ShowFrame(FrameStream fs)
    {
        Frame buf;
        lock (fs.Lock)
        {
            buf = fs.Pending;
            fs.Pending = null;
            fs.Posted = false;
        }
        if (buf == null) return;
        if (_items.TryGetValue(fs.Id, out Item it) && it.View == fs.View)
        {
            long start = System.Diagnostics.Stopwatch.GetTimestamp();
            Upload(fs.Y, buf.Planes[0], fs.W, fs.H);
            Upload(fs.U, buf.Planes[1], fs.W / 2, fs.H / 2);
            Upload(fs.V, buf.Planes[2], fs.W / 2, fs.H / 2);
            fs.UploadTicks += System.Diagnostics.Stopwatch.GetTimestamp() - start;
            fs.Shown++;
        }
        lock (fs.Lock) fs.Free.Push(buf);
    }

    // PixelData copies the bytes it is given, so a plane's array is reused.
    static void Upload(Texture texture, byte[] plane, int w, int h)
    {
        using (var pixels = new PixelData(plane, (uint)plane.Length, (uint)w, (uint)h, PixelFormat.L8, PixelData.ReleaseFunction.DeleteArray))
            texture.Upload(pixels);
    }

    static string ReadLine(Stream stream)
    {
        var bytes = new List<byte>();
        while (true)
        {
            int b = stream.ReadByte();
            if (b < 0) throw new IOException("closed");
            if (b == '\n') return Encoding.UTF8.GetString(bytes.ToArray());
            if (bytes.Count > 4096) throw new IOException("line too long");
            bytes.Add((byte)b);
        }
    }

    /// Reads `count` bytes; false if the stream ended before the first.
    static bool ReadFull(Stream stream, byte[] buf, int count)
    {
        int got = 0;
        while (got < count)
        {
            int n = stream.Read(buf, got, count - got);
            if (n == 0)
            {
                if (got == 0) return false;
                throw new IOException("partial read");
            }
            got += n;
        }
        return true;
    }

    static void Reply(Stream stream, string line)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(line + "\n");
        stream.Write(bytes, 0, bytes.Length);
    }
}
