using System;
using System.IO;
using System.Runtime.InteropServices;

/// <summary>
/// Drive SPK_SetAcmMode and watch whether Bluetooth audio routing comes back.
///
/// The routing layer is ACM (acm-service), not PulseAudio. bt_audio_connect
/// brings up the A2DP profile and returns 0, but audio stays on the internal
/// output because ACM still points there.
///
/// Signature from disassembly of SPK_SetAcmMode at 0x47ac740c (Thumb-2):
///
///     r0 = mode        -> saved to r4
///     returns int
///
/// One argument only. The numeric meaning of each mode is NOT known, so this
/// probe tries a range and, after each call, re-reads the authoritative state
/// signal:
///
///     db/btapp/lastSoundRoute   1 = internal, 5 = Bluetooth speaker
///
/// That is the only settings key that changes when the speaker connects
/// (established by a controlled connect/disconnect experiment), so it is the
/// verification signal here.
///
/// Run as root via the root channel; logs to /opt/cap/acmmode.log.
/// </summary>
internal static class Program
{
    const string Lib = "libacm-interface-api.so";
    const string Log = "/opt/cap/acmmode.log";

    [DllImport(Lib, EntryPoint = "SPK_SetAcmMode")]
    private static extern int SpkSetAcmMode(int mode);

    [DllImport(Lib, EntryPoint = "SPK_SetAcmMode_Lastmemory")]
    private static extern int SpkSetAcmModeLastMemory(int mode);

    private static void L(string s)
    {
        try { File.AppendAllText(Log, s + "\n"); } catch { }
    }

    private static int Main(string[] args)
    {
        try { File.WriteAllText(Log, "=== acmmode " + DateTime.Now + " ===\n"); } catch { }

        // Report the current route before touching anything.
        L("before: " + ReadRoute());

        // One mode per process: an invalid mode segfaults (observed at mode 1),
        // so the caller runs each value separately to isolate the crash.
        int[] modes;
        if (args.Length > 0 && int.TryParse(args[0], out int only))
            modes = new[] { only };
        else
            modes = new[] { 0 };

        foreach (int m in modes)
        {
            try
            {
                int rc = SpkSetAcmMode(m);
                L("SPK_SetAcmMode(" + m + ") rc=" + rc + " (0x" + rc.ToString("x") + ")");
            }
            catch (Exception ex)
            {
                L("SPK_SetAcmMode(" + m + ") threw " + ex.GetType().Name + ": " + ex.Message);
            }
            L("  after(" + m + "): " + ReadRoute());
        }

        L("done");
        return 0;
    }

    /// <summary>Read db/btapp/lastSoundRoute via buxton2ctl.</summary>
    private static string ReadRoute()
    {
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "/usr/bin/buxton2ctl",
                Arguments = "get system db/btapp/lastSoundRoute",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
            };
            using (var p = System.Diagnostics.Process.Start(psi))
            {
                string o = p.StandardOutput.ReadToEnd().Trim();
                p.WaitForExit(3000);
                int i = o.LastIndexOf("Int32:", StringComparison.Ordinal);
                return i >= 0 ? o.Substring(i).Trim() : o;
            }
        }
        catch (Exception ex) { return "read failed: " + ex.GetType().Name; }
    }
}
