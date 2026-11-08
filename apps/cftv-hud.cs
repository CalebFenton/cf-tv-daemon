using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Tizen.NUI;
using Tizen.NUI.BaseComponents;
using Color = Tizen.NUI.Color;
using Rectangle = Tizen.NUI.Rectangle;

/// <summary>
/// HUD: tracked targets drawn over the picture. `bin/hud live` (tv/hud.py)
/// sends one `hud` command per update listing every target.
///
/// All views share one full-screen transparent window, which keeps the HDMI
/// picture visible. All motion is NUI `Animation`, which runs on DALi's
/// update/render thread, so text layout (10-60 ms per changed label) or
/// command parsing on this event thread does not stall it.
///
/// A new id locks on, a known id glides to its new box at constant speed (the
/// host sends the box predicted for the end of the glide), and a missing id
/// fades out.
/// </summary>
public sealed partial class Cftv
{
    const string HudFontDir = Q + "fonts/";
    const string HudFont = "Share Tech Mono";
    const int ScreenH = 1080;                           // ScreenW is in cftv.cs
    const int Bar = 3, Thin = 1, Arm = 44;              // bracket line, chrome line, longest arm
    const float LineAlpha = 0.75f, ScanAlpha = 0.4f;    // brackets and accents; scan line peak (faint)
    const int LabelW = 380, LabelH = 62, LabelGap = 8;
    const int PanelW = 600, StatusLines = 4, LineH = 32;
    const int InsetW = 300, InsetImage = 284, InsetMs = 160;
    const int BoneW = 2, Dot = 3;
    const int LockMs = 420, FadeMs = 280, TypeMs = 360, ScanMs = 1000, GlideMs = 500;
    static readonly Color HudCyan = new Color(0f, 0.9f, 1f, 1f);
    static readonly Color HudPanel = new Color(0.01f, 0.07f, 0.1f, 0.72f);
    static readonly Color HudLine = new Color(0f, 0.9f, 1f, LineAlpha);

    sealed class HudTarget
    {
        public string Id, Title = "", Detail = "";
        public Color Tint;
        public View Area;                                   // the target's box; everything else hangs off it
        public readonly View[] HBars = new View[4], VBars = new View[4];  // TL, TR, BL, BR
        public View Label, Accent, TitleClip, Cursor;
        public TextLabel TitleLabel, DetailLabel;
        public Animation Motion, Reveal, PoseMotion, Spin;
        public bool Dying;
        public readonly List<View> Bones = new List<View>();  // skeleton: one thin rotated view per bone
        public readonly List<View> Dots = new List<View>();   // face landmark mesh
        public ImageView Reticle;
    }

    readonly Dictionary<string, HudTarget> _hud = new Dictionary<string, HudTarget>();
    Window _hudWin;
    View _hudRoot, _hudScanLine, _hudPanel, _hudPanelEdge;
    TextLabel _hudLive;
    readonly List<TextLabel> _hudLines = new List<TextLabel>();
    // ENHANCE inset: a magnified crop of the primary target, double-buffered so
    // a new image replaces the old one only once it has loaded.
    View _inset;
    TextLabel _insetTitle;
    readonly ImageView[] _insetImages = new ImageView[2];
    readonly string[] _insetFiles = new string[2];
    int _insetSeq, _insetFront;
    Animation _insetFade;
    float _insetTo;  // the opacity _inset is at or fading to
    const string ReticleSvg = "/tmp/cftv-reticle.svg";
    Animation _hudScan, _hudBlink;
    bool _fontLoaded, _hudOn;

    string Hud(JsonElement c)
    {
        if (Bool(c, "off", false))
        {
            HudOff();
            return Ok();
        }
        HudOn();
        int glide = Math.Max(1, Int(c, "glide", GlideMs));
        var seen = new HashSet<string>();
        if (c.TryGetProperty("targets", out JsonElement targets) && targets.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement e in targets.EnumerateArray())
            {
                string id = Str(e, "id", "");
                if (id.Length == 0) continue;
                seen.Add(id);
                int x = Int(e, "x", 0), y = Int(e, "y", 0);
                int w = Math.Max(Int(e, "w", 0), 8), h = Math.Max(Int(e, "h", 0), 8);
                if (_hud.TryGetValue(id, out HudTarget t) && !t.Dying)
                    Glide(t, x, y, w, h, glide);
                else
                {
                    if (t != null) Remove(t);  // fading out: lock on again
                    t = NewTarget(id);
                    _hud[id] = t;
                    LockOn(t, x, y, w, h);
                }
                SetText(t, Str(e, "title", ""), Str(e, "detail", ""), ParseColor(Str(e, "color", "#00E5FF")));
                Shape(t, x, y, w, h);
                Limbs(t, e, glide);
                Mesh(t, e);
                SetPrimary(t, Bool(e, "primary", false), x + w / 2, y + h / 2, Math.Min(w, h));
            }
        }
        foreach (HudTarget t in _hud.Values.ToList())
            if (!seen.Contains(t.Id) && !t.Dying) FadeOut(t);

        if (c.TryGetProperty("status", out JsonElement status) && status.ValueKind == JsonValueKind.Array)
            SetStatus(status.EnumerateArray().Select(l => l.GetString() ?? "").ToArray());
        if (c.TryGetProperty("inset", out JsonElement inset)) Inset(inset);
        if (Bool(c, "scan", true) && _hudScan.State != Animation.States.Playing) _hudScan.Play();
        return Ok(",\"targets\":" + _hud.Values.Count(t => !t.Dying));
    }

    // ------------------------------------------------------------ window and chrome

    void HudOn()
    {
        if (!_fontLoaded && Directory.Exists(HudFontDir))
        {
            try { FontClient.Instance.AddCustomFontDirectory(HudFontDir); _fontLoaded = true; }
            catch (Exception ex) { Log("font EX " + ex.Message); }
        }
        if (_hudOn) return;
        if (_hudWin == null) BuildHud();
        _hudWin.Show();
        Raise(_hudWin);
        _hudBlink.Play();
        _hudOn = true;
        Log("hud on");
    }

    /// Remove every target and hide the window; the window and chrome are
    /// kept for the next `hud`.
    void HudOff()
    {
        if (!_hudOn) return;
        foreach (HudTarget t in _hud.Values.ToList()) Remove(t);
        _hudScan.Stop();
        _hudScanLine.Opacity = 0f;
        _hudBlink.Stop();
        _insetFade?.Stop();
        _inset.Opacity = _insetTo = 0f;
        for (int i = 0; i < 2; i++)
        {
            _insetImages[i].Opacity = 0f;
            if (_insetFiles[i] != null) File.Delete(_insetFiles[i]);
            _insetFiles[i] = null;
        }
        _hudWin.Hide();
        _hudOn = false;
        Log("hud off");
    }

    void BuildHud()
    {
        _hudWin = new Window("cftv-hud", new Rectangle(0, 0, ScreenW, ScreenH), true);
        MakeOverlay(_hudWin);
        _hudRoot = new View { Size2D = new Size2D(ScreenW, ScreenH) };
        _hudWin.Add(_hudRoot);

        // Status panel, top left; it grows with the number of status lines.
        _hudPanel = new View { Position2D = new Position2D(56, 48), Size2D = new Size2D(PanelW, 64), BackgroundColor = HudPanel };
        _hudPanel.Add(new View { Size2D = new Size2D(PanelW, Thin), BackgroundColor = HudLine, BoxShadow = GlowOf(5f, HudLine) });
        _hudPanelEdge = new View { Size2D = new Size2D(Bar, 64), BackgroundColor = HudLine };
        _hudPanel.Add(_hudPanelEdge);
        _hudPanel.Add(HudText("CF-TV // OPTICAL ANALYSIS", 28f, HudCyan, 20, 12, 470, 36));
        _hudLive = HudText("\u25CF LIVE", 22f, new Color(1f, 0.25f, 0.2f, 1f), 486, 16, 100, 30);
        _hudPanel.Add(_hudLive);
        for (int i = 0; i < StatusLines; i++)
        {
            var line = HudText("", 21f, new Color(0.8f, 0.97f, 1f, i == 0 ? 0.95f : 0.75f), 20, 54 + i * LineH, PanelW - 30, 30);
            _hudLines.Add(line);
            _hudPanel.Add(line);
        }
        _hudRoot.Add(_hudPanel);

        // ENHANCE inset, top right: title over a magnified crop.
        _inset = new View
        {
            Position2D = new Position2D(ScreenW - 56 - InsetW, 48), Size2D = new Size2D(InsetW, InsetImage + 52),
            BackgroundColor = HudPanel, Opacity = 0f,
        };
        _inset.Add(new View { Size2D = new Size2D(InsetW, Thin), BackgroundColor = HudLine, BoxShadow = GlowOf(5f, HudLine) });
        _insetTitle = HudText("", 20f, HudCyan, 10, 8, InsetW - 20, 30);
        _inset.Add(_insetTitle);
        for (int i = 0; i < 2; i++)
        {
            var image = new ImageView { Position2D = new Position2D(8, 44), Size2D = new Size2D(InsetImage, InsetImage), Opacity = 0f };
            int slot = i;
            image.ResourceReady += (s, e) => InsetReady(slot);
            _insetImages[i] = image;
            _inset.Add(image);
        }
        _hudRoot.Add(_inset);

        // Reticle for the primary target: a ring with ticks, drawn from SVG
        // (NUI 5 views have no borders).
        File.WriteAllText(ReticleSvg,
            "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200' viewBox='0 0 200 200'>" +
            "<g fill='none' stroke='#FF3B30' stroke-opacity='0.85'>" +
            "<circle cx='100' cy='100' r='88' stroke-width='3' stroke-dasharray='46 23'/>" +
            "<circle cx='100' cy='100' r='70' stroke-width='1.5' stroke-opacity='0.5'/>" +
            "<path d='M100 2 V30 M100 170 V198 M2 100 H30 M170 100 H198' stroke-width='3'/>" +
            "<path d='M94 100 H106 M100 94 V106' stroke-width='2'/></g></svg>");

        var blink = new KeyFrames();
        blink.Add(0f, 1f);
        blink.Add(0.5f, 1f);
        blink.Add(0.51f, 0.25f);
        blink.Add(1f, 0.25f);
        _hudBlink = new Animation(1200) { Looping = true };
        _hudBlink.AnimateBetween(_hudLive, "Opacity", blink);

        // Frame ticks in the four screen corners.
        const int tick = 96, inset = 20;
        var dim = new Color(0f, 0.9f, 1f, 0.4f);
        for (int i = 0; i < 4; i++)
        {
            bool right = i % 2 == 1, bottom = i >= 2;
            int x = right ? ScreenW - inset - tick : inset, y = bottom ? ScreenH - inset - tick : inset;
            _hudRoot.Add(new View
            {
                Size2D = new Size2D(tick, Thin), BackgroundColor = dim, BoxShadow = GlowOf(4f, dim),
                Position2D = new Position2D(x, bottom ? y + tick - Thin : y),
            });
            _hudRoot.Add(new View
            {
                Size2D = new Size2D(Thin, tick), BackgroundColor = dim, BoxShadow = GlowOf(4f, dim),
                Position2D = new Position2D(right ? x + tick - Thin : x, y),
            });
        }

        // Scan line: sweeps down at constant speed and fades out at the end.
        _hudScanLine = new View
        {
            Size2D = new Size2D(ScreenW, Thin),
            BackgroundColor = new Color(0f, 0.9f, 1f, 0.7f), BoxShadow = GlowOf(6f, new Color(0f, 0.9f, 1f, 0.35f)),
            Opacity = 0f,
        };
        _hudRoot.Add(_hudScanLine);
        _hudScan = new Animation(ScanMs);
        var sweep = new KeyFrames();
        sweep.Add(0f, 0f);
        sweep.Add(1f, (float)ScreenH);
        _hudScan.AnimateBetween(_hudScanLine, "PositionY", sweep);
        var fade = new KeyFrames();
        fade.Add(0f, ScanAlpha);
        fade.Add(0.9f, ScanAlpha);
        fade.Add(1f, 0f);
        _hudScan.AnimateBetween(_hudScanLine, "Opacity", fade);
    }

    /// A blurred, unshifted shadow in the item's colour: the glow.
    static Shadow GlowOf(float blur, Color color) => new Shadow(blur, new Vector2(0, 0), color, new Vector2(0, 0));

    TextLabel HudText(string text, float size, Color color, int x, int y, int w, int h)
    {
        var label = new TextLabel
        {
            Text = text,
            PixelSize = size,
            TextColor = color,
            Position2D = new Position2D(x, y),
            Size2D = new Size2D(w, h),
            VerticalAlignment = VerticalAlignment.Center,
        };
        if (_fontLoaded) label.FontFamily = HudFont;
        return label;
    }

    /// Text layout costs 10-60 ms per change; skip unchanged text.
    static void SetLabel(TextLabel label, string text)
    {
        if (label.Text != text) label.Text = text;
    }

    // ------------------------------------------------------------ targets

    /// A target's views: a box with an L-shaped bracket pinned to each corner
    /// (ParentOrigin/PivotPoint), so animating the box's position and size
    /// moves and stretches the whole target; the label hangs off one corner.
    HudTarget NewTarget(string id)
    {
        // Anchoring by pivot needs PositionUsesPivotPoint; set every anchor explicitly.
        var t = new HudTarget { Id = id, Area = Anchored(ParentOrigin.TopLeft, PivotPoint.TopLeft) };
        t.Area.Opacity = 0f;
        Position[] corners = { ParentOrigin.TopLeft, ParentOrigin.TopRight, ParentOrigin.BottomLeft, ParentOrigin.BottomRight };
        for (int i = 0; i < 4; i++)
        {
            t.HBars[i] = Anchored(corners[i], corners[i]);
            t.VBars[i] = Anchored(corners[i], corners[i]);
            t.Area.Add(t.HBars[i]);
            t.Area.Add(t.VBars[i]);
        }
        t.Label = Anchored(ParentOrigin.TopLeft, PivotPoint.BottomLeft);
        t.Label.Size2D = new Size2D(LabelW, LabelH);
        t.Label.BackgroundColor = HudPanel;
        t.Accent = new View { Size2D = new Size2D(Bar, LabelH) };
        // The title is set once and revealed by widening its clip: typing it
        // letter by letter would cost one text layout per letter.
        t.TitleClip = new View
        {
            Position2D = new Position2D(16, 4), Size2D = new Size2D(0, 32),
            ClippingMode = ClippingModeType.ClipChildren,
        };
        t.TitleLabel = HudText("", 26f, HudCyan, 0, 0, LabelW - 24, 32);
        t.TitleClip.Add(t.TitleLabel);
        t.Cursor = new View { Size2D = new Size2D(12, 24), Position2D = new Position2D(16, 8), Opacity = 0f };
        t.DetailLabel = HudText("", 18f, new Color(0.8f, 0.97f, 1f, 0.85f), 16, 34, LabelW - 24, 24);
        t.Label.Add(t.Accent);
        t.Label.Add(t.TitleClip);
        t.Label.Add(t.Cursor);
        t.Label.Add(t.DetailLabel);
        t.Area.Add(t.Label);
        _hudRoot.Add(t.Area);
        return t;
    }

    static View Anchored(Position origin, Position pivot) =>
        new View { ParentOrigin = origin, PivotPoint = pivot, PositionUsesPivotPoint = true };

    /// Brackets close in from a box 60 % larger while fading up.
    static void LockOn(HudTarget t, int x, int y, int w, int h)
    {
        float gw = w * 0.6f, gh = h * 0.6f;
        t.Area.Position = new Position(x - gw / 2, y - gh / 2, 0);
        t.Area.Size = new Size(w + gw, h + gh, 0);
        t.Motion = new Animation(LockMs);
        t.Motion.AnimateTo(t.Area, "Position", new Position(x, y, 0), new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseOut));
        t.Motion.AnimateTo(t.Area, "Size", new Size(w, h, 0), new AlphaFunction(AlphaFunction.BuiltinFunctions.EaseOut));
        t.Motion.AnimateTo(t.Area, "Opacity", 1f, 0, (int)(LockMs * 0.6f));
        t.Motion.Play();
    }

    /// Constant speed from wherever the box is now, so consecutive glides join
    /// without stopping. Also completes a lock-on's fade-up if one is cut short.
    static void Glide(HudTarget t, int x, int y, int w, int h, int ms)
    {
        t.Motion?.Stop();
        t.Motion = new Animation(ms);
        t.Motion.AnimateTo(t.Area, "Position", new Position(x, y, 0), new AlphaFunction(AlphaFunction.BuiltinFunctions.Linear));
        t.Motion.AnimateTo(t.Area, "Size", new Size(w, h, 0), new AlphaFunction(AlphaFunction.BuiltinFunctions.Linear));
        t.Motion.AnimateTo(t.Area, "Opacity", 1f, 0, Math.Min(ms, 150));
        t.Motion.Play();
    }

    void FadeOut(HudTarget t)
    {
        t.Dying = true;
        t.Motion?.Stop();
        t.Motion = new Animation(FadeMs);
        t.Motion.AnimateTo(t.Area, "Opacity", 0f);
        t.Motion.Finished += (s, e) => { if (t.Dying) Remove(t); };
        t.Motion.Play();
    }

    void Remove(HudTarget t)
    {
        t.Dying = false;  // a pending fade's Finished must not remove it twice
        t.Motion?.Stop();
        t.Reveal?.Stop();
        t.PoseMotion?.Stop();
        t.Spin?.Stop();
        _hudRoot.Remove(t.Area);
        t.Area.Dispose();
        if (_hud.TryGetValue(t.Id, out HudTarget current) && current == t) _hud.Remove(t.Id);
    }

    void SetText(HudTarget t, string title, string detail, Color tint)
    {
        if (t.Tint == null || !t.Tint.Equals(tint))
        {
            t.Tint = tint;
            var line = new Color(tint.R, tint.G, tint.B, LineAlpha);
            var glow = new Color(tint.R, tint.G, tint.B, 0.45f);
            for (int i = 0; i < 4; i++)
            {
                t.HBars[i].BackgroundColor = line;
                t.VBars[i].BackgroundColor = line;
                t.HBars[i].BoxShadow = GlowOf(7f, glow);
                t.VBars[i].BoxShadow = GlowOf(7f, glow);
            }
            t.Accent.BackgroundColor = line;
            t.Cursor.BackgroundColor = tint;
            t.TitleLabel.TextColor = tint;
        }
        if (title != t.Title)
        {
            t.Title = title;
            SetLabel(t.TitleLabel, title);
            Reveal(t);
        }
        if (detail != t.Detail)
        {
            t.Detail = detail;
            SetLabel(t.DetailLabel, detail);
        }
    }

    /// Wipe the title in left to right behind a block cursor.
    static void Reveal(HudTarget t)
    {
        const int full = LabelW - 24;
        t.Reveal?.Stop();
        t.TitleClip.SizeWidth = 0;
        t.Cursor.PositionX = 16;
        t.Cursor.Opacity = 0.9f;
        t.Reveal = new Animation(TypeMs);
        t.Reveal.AnimateTo(t.TitleClip, "SizeWidth", (float)full);
        t.Reveal.AnimateTo(t.Cursor, "PositionX", 16f + full);
        t.Reveal.AnimateTo(t.Cursor, "Opacity", 0f, TypeMs - 1, TypeMs);
        t.Reveal.Play();
    }

    /// Bracket arms scale with the box, so small targets keep visible corners;
    /// the label sits above the box, or below it near the top of the screen,
    /// and right-aligned near the right edge.
    static void Shape(HudTarget t, int x, int y, int w, int h)
    {
        int arm = (int)Math.Max(14, Math.Min(Arm, Math.Min(w, h) * 0.28f));
        for (int i = 0; i < 4; i++)
        {
            if ((int)t.HBars[i].SizeWidth != arm) t.HBars[i].Size2D = new Size2D(arm, Bar);
            if ((int)t.VBars[i].SizeHeight != arm) t.VBars[i].Size2D = new Size2D(Bar, arm);
        }
        bool below = y < LabelH + LabelGap, right = x > ScreenW - LabelW;
        Position origin = below ? (right ? ParentOrigin.BottomRight : ParentOrigin.BottomLeft)
                                : (right ? ParentOrigin.TopRight : ParentOrigin.TopLeft);
        Position pivot = below ? (right ? PivotPoint.TopRight : PivotPoint.TopLeft)
                               : (right ? PivotPoint.BottomRight : PivotPoint.BottomLeft);
        t.Label.ParentOrigin = origin;
        t.Label.PivotPoint = pivot;
        t.Label.Position2D = new Position2D(0, below ? LabelGap : -LabelGap);
    }

    // ------------------------------------------------------------ status and inset

    void SetStatus(string[] lines)
    {
        int n = Math.Min(lines.Length, StatusLines);
        for (int i = 0; i < StatusLines; i++) SetLabel(_hudLines[i], i < n ? lines[i] : "");
        int h = 64 + n * LineH;
        if ((int)_hudPanel.SizeHeight != h)
        {
            _hudPanel.SizeHeight = h;
            _hudPanelEdge.SizeHeight = h;
        }
    }

    /// `inset`: {"jpeg": base64, "title": text}, or null to hide the panel.
    /// The image goes through a file in /tmp (tmpfs; NUI 5 has no image from
    /// memory) into the hidden ImageView, which is shown once it has loaded.
    void Inset(JsonElement inset)
    {
        if (inset.ValueKind != JsonValueKind.Object)
        {
            FadeInset(0f);
            return;
        }
        string data = Str(inset, "jpeg", "");
        if (data.Length > 0)
        {
            int back = 1 - _insetFront;
            string file = "/tmp/cftv-inset-" + ++_insetSeq + ".jpg";
            File.WriteAllBytes(file, Convert.FromBase64String(data));
            string old = _insetFiles[back];
            _insetFiles[back] = file;
            _insetImages[back].ResourceUrl = file;
            if (old != null) File.Delete(old);
        }
        SetLabel(_insetTitle, Str(inset, "title", ""));
        FadeInset(1f);
    }

    void InsetReady(int slot)
    {
        if (slot == _insetFront && _insetImages[slot].Opacity > 0f) return;
        _insetImages[slot].Opacity = 1f;
        _insetImages[1 - slot].Opacity = 0f;
        _insetFront = slot;
    }

    void FadeInset(float to)
    {
        if (to == _insetTo) return;
        _insetTo = to;
        _insetFade?.Stop();
        _insetFade = new Animation(InsetMs);
        _insetFade.AnimateTo(_inset, "Opacity", to);
        _insetFade.Play();
    }

    // ------------------------------------------------------------ skeleton, mesh, primary

    /// `bones`: [[x0, y0, x1, y1], ...] relative to the target's box. Each bone
    /// is a thin view pinned at its left end and rotated; it glides with the box.
    static void Limbs(HudTarget t, JsonElement e, int glide)
    {
        int n = e.TryGetProperty("bones", out JsonElement bones) && bones.ValueKind == JsonValueKind.Array
            ? bones.GetArrayLength() : 0;
        if (n == 0 && t.Bones.Count == 0) return;
        int existing = t.Bones.Count;
        while (t.Bones.Count < n)
        {
            View v = Anchored(ParentOrigin.TopLeft, PivotPoint.CenterLeft);
            v.BackgroundColor = new Color(t.Tint.R, t.Tint.G, t.Tint.B, 0.6f);
            v.CornerRadius = BoneW / 2f;
            t.Area.Add(v);
            t.Bones.Add(v);
        }
        t.PoseMotion?.Stop();
        t.PoseMotion = new Animation(glide);
        int i = 0;
        if (n > 0) foreach (JsonElement b in bones.EnumerateArray())
        {
            float x0 = b[0].GetSingle(), y0 = b[1].GetSingle(), x1 = b[2].GetSingle(), y1 = b[3].GetSingle();
            var at = new Position(x0, y0, 0);
            var size = new Size((float)Math.Sqrt((x1 - x0) * (x1 - x0) + (y1 - y0) * (y1 - y0)), BoneW, 0);
            var turn = new Rotation(new Radian((float)Math.Atan2(y1 - y0, x1 - x0)), Vector3.ZAxis);
            View v = t.Bones[i];
            if (i >= existing || v.Opacity == 0f)
            {
                v.Position = at;
                v.Size = size;
                v.Orientation = turn;
                v.Opacity = 1f;
            }
            else
            {
                t.PoseMotion.AnimateTo(v, "Position", at);
                t.PoseMotion.AnimateTo(v, "Size", size);
                t.PoseMotion.AnimateTo(v, "Orientation", turn);
            }
            i++;
        }
        for (; i < t.Bones.Count; i++) t.Bones[i].Opacity = 0f;
        t.PoseMotion.Play();
    }

    /// `mesh`: [x, y, x, y, ...] face landmarks relative to the target's box, as dots.
    static void Mesh(HudTarget t, JsonElement e)
    {
        int n = e.TryGetProperty("mesh", out JsonElement mesh) && mesh.ValueKind == JsonValueKind.Array
            ? mesh.GetArrayLength() / 2 : 0;
        if (n == 0 && t.Dots.Count == 0) return;
        while (t.Dots.Count < n)
        {
            View v = Anchored(ParentOrigin.TopLeft, PivotPoint.Center);
            v.Size2D = new Size2D(Dot, Dot);
            v.CornerRadius = Dot / 2f;
            v.BackgroundColor = new Color(t.Tint.R, t.Tint.G, t.Tint.B, 0.55f);
            t.Area.Add(v);
            t.Dots.Add(v);
        }
        for (int i = 0; i < t.Dots.Count; i++)
        {
            View v = t.Dots[i];
            if (i < n)
            {
                v.Position2D = new Position2D(mesh[2 * i].GetInt32(), mesh[2 * i + 1].GetInt32());
                v.Opacity = 1f;
            }
            else v.Opacity = 0f;
        }
    }

    /// The primary target gets a spinning reticle and leaves a fading trail.
    void SetPrimary(HudTarget t, bool primary, int cx, int cy, int size)
    {
        if (!primary)
        {
            if (t.Reticle != null) t.Reticle.Opacity = 0f;
            return;
        }
        if (t.Reticle == null)
        {
            t.Reticle = new ImageView
            {
                ResourceUrl = ReticleSvg, ParentOrigin = ParentOrigin.Center, PivotPoint = PivotPoint.Center,
                PositionUsesPivotPoint = true,
            };
            t.Area.Add(t.Reticle);
            var spin = new KeyFrames();
            for (int i = 0; i <= 3; i++) spin.Add(i / 3f, new Rotation(new Radian(new Degree(120f * i)), Vector3.ZAxis));
            t.Spin = new Animation(6000) { Looping = true };
            t.Spin.AnimateBetween(t.Reticle, "Orientation", spin);
            t.Spin.Play();
        }
        int s = Math.Max(48, (int)(size * 0.9f));
        t.Reticle.Size2D = new Size2D(s, s);
        t.Reticle.Opacity = 1f;
        Trail(cx, cy);
    }

    readonly HashSet<Animation> _trails = new HashSet<Animation>();

    void Trail(int x, int y)
    {
        View dot = Anchored(ParentOrigin.TopLeft, PivotPoint.Center);
        dot.Position2D = new Position2D(x, y);
        dot.Size2D = new Size2D(6, 6);
        dot.CornerRadius = 3f;
        dot.BackgroundColor = new Color(1f, 0.23f, 0.19f, 0.7f);
        _hudRoot.Add(dot);
        var fade = new Animation(600);
        fade.AnimateTo(dot, "Opacity", 0f);
        fade.AnimateTo(dot, "Size", new Size(2, 2, 0));
        fade.Finished += (s, e) =>
        {
            _trails.Remove(fade);
            _hudRoot.Remove(dot);
            dot.Dispose();
        };
        _trails.Add(fade);  // referenced until it ends
        fade.Play();
    }
}
