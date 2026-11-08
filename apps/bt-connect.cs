using System;
using System.IO;
using System.Runtime.InteropServices;

/// <summary>
/// Call bt_audio_connect directly, bypassing the D-Bus layer.
///
/// Why this exists: the D-Bus route (org.projectx.bt service_request) requires
/// constants we could not recover reliably, and bt.autoconnect's
/// PrePareAndDoAutoConnect is filtered out for our sender. Calling the exported
/// C function removes both variables: the dynamic loader resolves the symbol,
/// so no hand-computed offsets are involved, and there is no D-Bus identity
/// check.
///
/// Signature, from disassembly of bt_audio_connect at 0x4636f7a0 (Thumb-2):
///
///     r0 = address string   (saved to r6)
///     r1 = mode selector    (saved to r5)
///
/// r1 is compared against 0, 1 and 2 early in the function, so it is an enum.
/// The meaning of each value is NOT known; this probe tries them in turn and
/// reports the return code for each so the mapping can be established
/// empirically instead of guessed.
///
/// Note on init: the function begins with a call that behaves like an
/// init-status check (_bt_check_init_status). If the library is not
/// initialized in this process we expect an early error rather than a
/// connection attempt. bt_audio_initialize is exported, so this probe calls it
/// first and reports what it returns.
///
/// Run as root via the root channel, with the speaker as `addr=AA:BB:CC:DD:EE:FF`.
/// Writes a log so the result survives without the screen.
/// </summary>
internal static class Program
{
    const string Lib = "libcapi-network-bluetooth.so.0";
    const string Log = "/opt/cap/btconnect.log";
    static string Target = "";

    [DllImport(Lib, EntryPoint = "bt_audio_connect")]
    private static extern int BtAudioConnect(string address, int mode);

    [DllImport(Lib, EntryPoint = "bt_audio_disconnect")]
    private static extern int BtAudioDisconnect(string address, int mode);

    [DllImport(Lib, EntryPoint = "bt_audio_initialize")]
    private static extern int BtAudioInitialize();

    // Library-level init. bt_audio_connect returns 0xfe400101 (NOT_INITIALIZED)
    // until this has run; verified by direct call.
    // bt.connectionmanager runs with SMACK label System::Privileged. Writing
    // that label to our own attr/current succeeds while we hold CAP_MAC_ADMIN
    // (verified), so the probe can adopt the same label before connecting.
    // This is the last visible difference between our call and the working one.
    [DllImport("libc", EntryPoint = "open", SetLastError = true)]
    private static extern int SysOpen(string path, int flags);

    [DllImport("libc", EntryPoint = "write", SetLastError = true)]
    private static extern int SysWrite(int fd, byte[] buf, int count);

    [DllImport("libc", EntryPoint = "close", SetLastError = true)]
    private static extern int SysClose(int fd);

    [DllImport(Lib, EntryPoint = "bt_initialize")]
    private static extern int BtInitialize();

    [DllImport(Lib, EntryPoint = "bt_deinitialize")]
    private static extern int BtDeinitialize();

    // bt.connectionmanager calls this before connecting (string evidence), and
    // the manual-connect trace shows DiscoveryFinished immediately before
    // DeviceConnected. Stop discovery first, matching the working path.
    [DllImport(Lib, EntryPoint = "bt_adapter_stop_device_discovery")]
    private static extern int BtAdapterStopDiscovery();

    [DllImport(Lib, EntryPoint = "bt_adapter_is_discovering")]
    private static extern int BtAdapterIsDiscovering(out int discovering);

    // bt.connectionmanager registers these before it connects. A bare
    // bt_audio_connect from an anonymous client brings up the ACL but the A2DP
    // sink never registers, so bt-service may require a profile client.
    // Signatures from disassembly: both take (callback, user_data).
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void AudioStateCb(string address, int state, IntPtr userData);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void DeviceStateCb(string address, int state, IntPtr userData);

    [DllImport(Lib, EntryPoint = "bt_audio_set_connection_state_changed_cb")]
    private static extern int SetAudioCb(AudioStateCb cb, IntPtr userData);

    [DllImport(Lib, EntryPoint = "bt_device_set_connection_state_changed_cb")]
    private static extern int SetDeviceCb(DeviceStateCb cb, IntPtr userData);

    // Keep delegates alive for the process lifetime: the native side holds
    // raw pointers and a collected delegate would crash on callback.
    private static AudioStateCb _audioCb;
    private static DeviceStateCb _deviceCb;

    [DllImport(Lib, EntryPoint = "bt_adapter_get_address")]
    private static extern int BtAdapterGetAddress(out IntPtr address);

    private static void L(string s)
    {
        try { File.AppendAllText(Log, s + "\n"); } catch { }
    }

    private static int Main(string[] args)
    {
        try { File.WriteAllText(Log, "=== btconnect " + DateTime.Now + " ===\n"); } catch { }
        foreach (string a in args)
        {
            if (a.StartsWith("addr=", StringComparison.Ordinal))
                Target = a.Substring(5);
        }
        if (Target.Length == 0)
        {
            L("usage: BtConnect addr=AA:BB:CC:DD:EE:FF [mode=N] [hold=SECONDS]");
            return 2;
        }
        L("target=" + Target);

        // Report which library file actually got loaded, so we know we are
        // talking to the TV's copy and not something bundled with the app.
        try
        {
            foreach (System.Diagnostics.ProcessModule m in
                     System.Diagnostics.Process.GetCurrentProcess().Modules)
            {
                if (m.ModuleName.IndexOf("bluetooth", StringComparison.OrdinalIgnoreCase) >= 0)
                    L("loaded: " + m.FileName);
            }
        }
        catch (Exception ex) { L("module list failed: " + ex.GetType().Name); }

        try
        {
            int rc = BtAdapterGetAddress(out IntPtr p);
            L("adapter_get_address rc=" + rc + (p != IntPtr.Zero
                ? " addr=" + Marshal.PtrToStringAnsi(p) : ""));
        }
        catch (Exception ex) { L("adapter_get_address threw " + ex.GetType().Name); }

        try
        {
            int rc = BtInitialize();
            L("bt_initialize rc=" + rc + "  (0x" + rc.ToString("x") + ")");
        }
        catch (Exception ex) { L("bt_initialize threw " + ex.GetType().Name); }

        try
        {
            int rc = BtAudioInitialize();
            L("audio_initialize rc=" + rc + "  (0x" + rc.ToString("x") + ")");
        }
        catch (Exception ex) { L("audio_initialize threw " + ex.GetType().Name); }

        // Try each selector. mode 0 first: the function's own dispatch checks
        // r5==0 last, which in a switch chain usually means "default".
        // bt.connectionmanager calls stop_device_discovery before connecting,
        // and the manual-connect trace shows DiscoveryFinished immediately
        // before DeviceConnected. Match the working path.
        try
        {
            BtAdapterIsDiscovering(out int disc);
            L("is_discovering=" + disc);
            int rcStop = BtAdapterStopDiscovery();
            L("stop_discovery rc=" + rcStop + " (0x" + rcStop.ToString("x") + ")");
            BtAdapterIsDiscovering(out disc);
            L("is_discovering after=" + disc);
        }
        catch (Exception ex) { L("discovery calls threw " + ex.GetType().Name); }

        try
        {
            _audioCb = (addr, state, ud) => { L("  [cb] audio " + addr + " state=" + state); };
            _deviceCb = (addr, state, ud) => { L("  [cb] device " + addr + " state=" + state); };
            int rcA = SetAudioCb(_audioCb, IntPtr.Zero);
            L("set_audio_cb rc=" + rcA + " (0x" + rcA.ToString("x") + ")");
            int rcD = SetDeviceCb(_deviceCb, IntPtr.Zero);
            L("set_device_cb rc=" + rcD + " (0x" + rcD.ToString("x") + ")");
        }
        catch (Exception ex) { L("callback registration threw " + ex.GetType().Name + ": " + ex.Message); }

        try
        {
            byte[] label = System.Text.Encoding.ASCII.GetBytes("System::Privileged");
            int fd = SysOpen("/proc/self/attr/current", 1); // O_WRONLY
            if (fd >= 0)
            {
                int n = SysWrite(fd, label, label.Length);
                SysClose(fd);
                L("smack label write: " + (n == label.Length ? "OK" : "short write " + n));
            }
            else L("smack label open failed");
        }
        catch (Exception ex) { L("smack label threw " + ex.GetType().Name); }

        // Two variables were never tested before:
        //
        //  1. The probe exited milliseconds after connecting. A previous run
        //     registered both state callbacks and NEITHER fired, while
        //     dbus-monitor saw DeviceConnected/DeviceDisconnected. bt-service
        //     may tie the connection to the client's lifetime, so a client that
        //     vanishes gets its ACL torn down.
        //  2. It called connect three times back to back. The 2nd and 3rd calls
        //     may tear down the 1st - mode 0/1/2 "all failing identically" is
        //     equally consistent with "each call kills the previous".
        //
        // So: connect ONCE, then stay alive and watch for callbacks.
        int mode = 0;
        foreach (string a in args)
        {
            if (a.StartsWith("mode=", StringComparison.Ordinal))
                int.TryParse(a.Substring(5), out mode);
        }

        int holdSeconds = 60;
        foreach (string a in args)
        {
            if (a.StartsWith("hold=", StringComparison.Ordinal))
                int.TryParse(a.Substring(5), out holdSeconds);
        }

        L("connecting once: mode=" + mode + " hold=" + holdSeconds + "s");
        try
        {
            int rc = BtAudioConnect(Target, mode);
            L("audio_connect(mode=" + mode + ") rc=" + rc + "  (0x" +
              rc.ToString("x") + ")  <- queued, not connected");
        }
        catch (Exception ex)
        {
            L("audio_connect threw " + ex.GetType().Name + ": " + ex.Message);
        }

        // Stay alive so bt-service keeps seeing a live client, and so the
        // callbacks (which fire on a native thread) can actually run.
        for (int t = 0; t < holdSeconds; t++)
        {
            System.Threading.Thread.Sleep(1000);
            if (t % 10 == 9) L("  ... alive " + (t + 1) + "s");
        }

        L("done (held " + holdSeconds + "s)");
        return 0;
    }
}
