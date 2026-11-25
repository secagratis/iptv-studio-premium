import concurrent.futures
import datetime
import gzip
import json
import os
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color as KivyColor, Line
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import FocusBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.recyclegridlayout import RecycleGridLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

Window.clearcolor = (0.05, 0.08, 0.15, 1)

PRESETS = {
    "Seca (Italia MIX)": "https://raw.githubusercontent.com/secagratis/Lista-Rai-IPTV/refs/heads/main/MIX.M3U",
    "Vassel81 (Italia)": "https://raw.githubusercontent.com/Vassel81/iptv-italia-personale/refs/heads/main/italia-unificata.m3u",
    "Pluto TV Italia": "https://raw.githubusercontent.com/secagratis/Lista-Rai-IPTV/refs/heads/main/Pluto.m3u",
    "Tvitava": "https://raw.githubusercontent.com/bbb0six/tvitava/refs/heads/main/tvv.m3u8",
    "Rakuten TV Italia": "https://raw.githubusercontent.com/iptv-org/iptv/refs/heads/master/streams/it_rakuten.m3u",
    "iptv-org (Globale)": "https://iptv-org.github.io/iptv/index.m3u",
}

EPG_PRESETS = {
    "EPG Italia (iptv-epg)": "https://iptv-epg.org/files/epg-it.xml.gz",
    "EPG Italia (iptv-org)": "https://iptv-org.github.io/epg/guides/it.xml",
    "EPG Globale (iptv-org)": "https://iptv-org.github.io/epg/guides/en.xml",
}

PLAYER_OPTIONS = [
    "Player: ExoPlayer",
    "Player: Scelta App",
    "Player: MX Player",
    "Player: VLC",
    "Player: Nova Player",
]

DEVICE_PROFILES = {
    "Layout: Box TV": {
        "short": "Box TV (Telecomando)",
        "cols": 2,
        "font_scale": 1.28,
        "pad": 16,
        "gap": 10,
        "hdr_h": 44,
        "ctrl_row_h": 50,
        "row_h": 96,
        "epg_h": 22,
        "btn_w_sm": 80,
        "btn_w_md": 108,
        "btn_w_lg": 122,
        "player_w": 182,
        "login_pad": 26,
        "input_h": 52,
        "action_h": 56,
    },
    "Layout: Tablet": {
        "short": "Tablet (2 Colonne)",
        "cols": 2,
        "font_scale": 1.22,
        "pad": 14,
        "gap": 10,
        "hdr_h": 42,
        "ctrl_row_h": 48,
        "row_h": 92,
        "epg_h": 21,
        "btn_w_sm": 72,
        "btn_w_md": 100,
        "btn_w_lg": 114,
        "player_w": 170,
        "login_pad": 28,
        "input_h": 52,
        "action_h": 56,
    },
    "Layout: Telefono": {
        "short": "Telefono",
        "cols": 1,
        "font_scale": 1.0,
        "pad": 7,
        "gap": 5,
        "hdr_h": 30,
        "ctrl_row_h": 38,
        "row_h": 66,
        "epg_h": 15,
        "btn_w_sm": 52,
        "btn_w_md": 74,
        "btn_w_lg": 86,
        "player_w": 126,
        "login_pad": 16,
        "input_h": 42,
        "action_h": 46,
    },
}

DEFAULT_DEVICE_PROFILE = "Layout: Box TV"

PLAYER_PACKAGES = {
    "Player: MX Player": ["com.mxtech.videoplayer.pro", "com.mxtech.videoplayer.ad"],
    "Player: VLC": ["org.videolan.vlc"],
    "Player: Nova Player": ["org.courville.nova"],
}

DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13; TV Box) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

DEFAULT_APP_USER = "admin"
DEFAULT_APP_PASS = "1234"

_TAG_SUFFIX_RE = re.compile(r"\b(4k|uhd|fhd|hd|sd|hevc|h265|h264|it|ita)\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def is_wide_layout(profile_name: str | None) -> bool:
    return profile_name in ("Layout: Box TV", "Layout: Tablet")


def get_profile_config(profile_name: str | None = None) -> dict:
    key = profile_name if profile_name in DEVICE_PROFILES else DEFAULT_DEVICE_PROFILE
    return DEVICE_PROFILES[key]


def scaled_sp(base_sp: float, profile_name: str | None = None) -> str:
    cfg = get_profile_config(profile_name)
    val = round(base_sp * cfg["font_scale"], 1)
    return f"{val}sp"


def normalize_channel_key(name: str) -> str:
    low = (name or "").strip().lower()
    low = _TAG_SUFFIX_RE.sub(" ", low)
    return _NON_ALNUM_RE.sub("", low)


def make_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def resolve_stream_url(url: str, ua: str = "", ssl_ctx=None) -> tuple[str, str]:
    clean = (url or "").strip()
    if not clean.lower().startswith(("http://", "https://")):
        return clean, ua

    parsed = urllib.parse.urlparse(clean)
    qs = urllib.parse.parse_qs(parsed.query)
    forced_ua = (qs.get("forceUserAgent", [""])[0]).strip()
    effective_ua = ua or forced_ua or DEFAULT_UA

    needs_resolve = (
        "relinker" in clean.lower()
        or parsed.path.lower().endswith((".htm", ".html", ".php", ".aspx"))
        or bool(forced_ua)
    )
    if not needs_resolve:
        return clean, effective_ua

    try:
        req = urllib.request.Request(
            clean,
            headers={"User-Agent": effective_ua, "Accept": "*/*", "Connection": "close"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=7, context=ssl_ctx or make_ssl_context()) as resp:
            final_url = resp.geturl() or clean
            return final_url, effective_ua
    except Exception:
        return clean, effective_ua


def parse_xmltv_time(ts: str):
    if not ts:
        return None
    m = re.match(r"^(\d{8,14})\s*([+-]\d{4})?$", ts.strip())
    if not m:
        return None
    digits = m.group(1).ljust(14, "0")[:14]
    try:
        dt = datetime.datetime.strptime(digits, "%Y%m%d%H%M%S")
        tz_str = m.group(2)
        if tz_str:
            sign = 1 if tz_str[0] == "+" else -1
            off_h = int(tz_str[1:3])
            off_m = int(tz_str[3:5])
            tz = datetime.timezone(datetime.timedelta(hours=sign * off_h, minutes=sign * off_m))
            dt = dt.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


class AndroidExoPlayerWindow:
    """Finestra nativa Android (Dialog) con AndroidX Media3 ExoPlayer e supporto telecomando TV."""

    def __init__(self, on_prev=None, on_next=None, on_external=None, on_closed=None):
        self._on_prev = on_prev
        self._on_next = on_next
        self._on_external = on_external
        self._on_closed = on_closed

        self._dialog = None
        self._exo = None
        self._texture_view = None
        self._fallback_vv = None
        self._title_view = None
        self._status_view = None
        self._controls_bar = None
        self._top_bar = None
        self._btn_play_pause = None
        self._btn_aspect = None
        self._stretch_mode = False
        self._controls_visible = True
        self._closing_guard = False
        self._using_exo = False
        self._jni_listeners = []

    def _clear_refs(self):
        self._dialog = None
        self._exo = None
        self._texture_view = None
        self._fallback_vv = None
        self._title_view = None
        self._status_view = None
        self._top_bar = None
        self._controls_bar = None
        self._btn_play_pause = None
        self._btn_aspect = None
        self._using_exo = False
        self._jni_listeners = []

    def open_or_update(self, url: str, title: str, ua: str = "", referer: str = "", epg_info: str = ""):
        sub_txt = epg_info or url
        eff_ua = ua or DEFAULT_UA

        if platform != "android":
            return

        try:
            from android.runnable import run_on_ui_thread
            from jnius import PythonJavaClass, autoclass, java_method

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Dialog = autoclass("android.app.Dialog")
            TextureView = autoclass("android.view.TextureView")
            FrameLayout = autoclass("android.widget.FrameLayout")
            LinearLayout = autoclass("android.widget.LinearLayout")
            TextView = autoclass("android.widget.TextView")
            Button = autoclass("android.widget.Button")
            Gravity = autoclass("android.view.Gravity")
            Color = autoclass("android.graphics.Color")
            TextUtils = autoclass("android.text.TextUtils")
            Uri = autoclass("android.net.Uri")
            String = autoclass("java.lang.String")
            HashMap = autoclass("java.util.HashMap")
            WindowManager = autoclass("android.view.WindowManager")
            KeyEvent = autoclass("android.view.KeyEvent")
            LP_Frame = autoclass("android.widget.FrameLayout$LayoutParams")
            LP_Linear = autoclass("android.widget.LinearLayout$LayoutParams")

            exo_available = False
            try:
                ExoPlayerBuilder = autoclass("androidx.media3.exoplayer.ExoPlayer$Builder")
                MediaItem = autoclass("androidx.media3.common.MediaItem")
                DefaultHttpDataSourceFactory = autoclass("androidx.media3.datasource.DefaultHttpDataSource$Factory")
                DefaultMediaSourceFactory = autoclass("androidx.media3.exoplayer.source.DefaultMediaSourceFactory")
                exo_available = True
            except Exception:
                exo_available = False

            activity = PythonActivity.mActivity
            owner = self

            class ClickProxy(PythonJavaClass):
                __javainterfaces__ = ["android.view.View$OnClickListener"]
                __javacontext__ = "app"

                def __init__(self, action_name):
                    super().__init__()
                    self.action_name = action_name

                @java_method("(Landroid/view/View;)V")
                def onClick(self, view):
                    if self.action_name == "close":
                        Clock.schedule_once(lambda *_: owner.close(), 0)
                    elif self.action_name == "toggle_play":
                        Clock.schedule_once(lambda *_: owner.toggle_play_pause(), 0)
                    elif self.action_name == "aspect":
                        Clock.schedule_once(lambda *_: owner.toggle_aspect(), 0)
                    elif self.action_name == "bars":
                        Clock.schedule_once(lambda *_: owner.toggle_controls_bar(), 0)
                    elif self.action_name == "prev" and owner._on_prev:
                        Clock.schedule_once(lambda *_: owner._on_prev(), 0)
                    elif self.action_name == "next" and owner._on_next:
                        Clock.schedule_once(lambda *_: owner._on_next(), 0)
                    elif self.action_name == "external" and owner._on_external:
                        Clock.schedule_once(lambda *_: owner._on_external(), 0)

            class DialogKeyProxy(PythonJavaClass):
                __javainterfaces__ = ["android.content.DialogInterface$OnKeyListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/content/DialogInterface;ILandroid/view/KeyEvent;)Z")
                def onKey(self, dialog_interface, key_code, event):
                    if event.getAction() != KeyEvent.ACTION_DOWN:
                        return False

                    if key_code == KeyEvent.KEYCODE_BACK:
                        Clock.schedule_once(lambda *_: owner.close(), 0)
                        return True
                    if key_code in (KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_PAGE_UP):
                        if owner._on_next:
                            Clock.schedule_once(lambda *_: owner._on_next(), 0)
                        return True
                    if key_code in (KeyEvent.KEYCODE_CHANNEL_DOWN, KeyEvent.KEYCODE_PAGE_DOWN):
                        if owner._on_prev:
                            Clock.schedule_once(lambda *_: owner._on_prev(), 0)
                        return True
                    if key_code in (KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE):
                        Clock.schedule_once(lambda *_: owner.toggle_play_pause(), 0)
                        return True
                    if key_code in (KeyEvent.KEYCODE_MENU, KeyEvent.KEYCODE_INFO):
                        Clock.schedule_once(lambda *_: owner.toggle_controls_bar(), 0)
                        return True
                    if not owner._controls_visible and key_code in (
                        KeyEvent.KEYCODE_DPAD_CENTER,
                        KeyEvent.KEYCODE_ENTER,
                        KeyEvent.KEYCODE_DPAD_UP,
                        KeyEvent.KEYCODE_DPAD_DOWN,
                        KeyEvent.KEYCODE_DPAD_LEFT,
                        KeyEvent.KEYCODE_DPAD_RIGHT,
                    ):
                        Clock.schedule_once(lambda *_: owner.toggle_controls_bar(), 0)
                        return True
                    return False

            class DismissProxy(PythonJavaClass):
                __javainterfaces__ = ["android.content.DialogInterface$OnDismissListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/content/DialogInterface;)V")
                def onDismiss(self, dialog_interface):
                    try:
                        if owner._exo is not None:
                            owner._exo.stop()
                            owner._exo.release()
                    except Exception:
                        pass
                    try:
                        if owner._fallback_vv is not None:
                            owner._fallback_vv.stopPlayback()
                    except Exception:
                        pass
                    owner._clear_refs()
                    if not owner._closing_guard and owner._on_closed:
                        Clock.schedule_once(lambda *_: owner._on_closed(None), 0)

            def bind_click(view, action_name):
                listener = ClickProxy(action_name)
                owner._jni_listeners.append(listener)
                view.setOnClickListener(listener)

            @run_on_ui_thread
            def build_or_play():
                if owner._dialog is None:
                    owner._closing_guard = False
                    owner._controls_visible = True
                    owner._jni_listeners = []
                    dlg = Dialog(activity, autoclass("android.R$style").Theme_Black_NoTitleBar_Fullscreen)
                    win = dlg.getWindow()
                    if win is not None:
                        win.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        win.setLayout(
                            WindowManager.LayoutParams.MATCH_PARENT,
                            WindowManager.LayoutParams.MATCH_PARENT,
                        )

                    root = FrameLayout(activity)
                    root.setBackgroundColor(Color.BLACK)

                    if exo_available:
                        tex = TextureView(activity)
                        bind_click(tex, "bars")
                        root.addView(tex, LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.MATCH_PARENT, Gravity.CENTER))

                        http_factory = DefaultHttpDataSourceFactory()
                        http_factory.setUserAgent(String(eff_ua))
                        http_factory.setAllowCrossProtocolRedirects(True)
                        http_factory.setConnectTimeoutMs(10000)
                        http_factory.setReadTimeoutMs(15000)
                        if referer:
                            hdrs = HashMap()
                            hdrs.put(String("Referer"), String(referer))
                            http_factory.setDefaultRequestProperties(hdrs)

                        media_source_factory = DefaultMediaSourceFactory(http_factory)
                        exo_builder = ExoPlayerBuilder(activity)
                        exo_builder.setMediaSourceFactory(media_source_factory)
                        exo = exo_builder.build()
                        exo.setVideoTextureView(tex)

                        owner._exo = exo
                        owner._texture_view = tex
                        owner._using_exo = True
                    else:
                        VideoView = autoclass("android.widget.VideoView")
                        vv = VideoView(activity)
                        vv.setZOrderMediaOverlay(True)
                        bind_click(vv, "bars")
                        root.addView(vv, LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.MATCH_PARENT, Gravity.CENTER))
                        owner._fallback_vv = vv
                        owner._using_exo = False

                    top_bar = LinearLayout(activity)
                    top_bar.setOrientation(LinearLayout.HORIZONTAL)
                    top_bar.setGravity(Gravity.CENTER_VERTICAL)
                    top_bar.setBackgroundColor(Color.argb(220, 15, 23, 42))
                    top_bar.setPadding(18, 12, 18, 12)

                    btn_top_close = Button(activity)
                    btn_top_close.setText(String("✕ Chiudi"))
                    btn_top_close.setTextColor(Color.WHITE)
                    btn_top_close.setBackgroundColor(Color.argb(245, 220, 38, 38))
                    btn_top_close.setSingleLine(True)
                    btn_top_close.setFocusable(True)
                    btn_top_close.setPadding(18, 8, 18, 8)
                    bind_click(btn_top_close, "close")
                    lp_tc = LP_Linear(LP_Linear.WRAP_CONTENT, LP_Linear.WRAP_CONTENT)
                    lp_tc.rightMargin = 14
                    top_bar.addView(btn_top_close, lp_tc)

                    title_box = LinearLayout(activity)
                    title_box.setOrientation(LinearLayout.VERTICAL)
                    title_lp = LP_Linear(0, LP_Linear.WRAP_CONTENT, 1.0)

                    tv_title = TextView(activity)
                    tv_title.setTextColor(Color.WHITE)
                    tv_title.setTextSize(16.0)
                    tv_title.setSingleLine(True)
                    tv_title.setEllipsize(TextUtils.TruncateAt.END)

                    tv_sub = TextView(activity)
                    tv_sub.setTextColor(Color.argb(230, 125, 211, 252))
                    tv_sub.setTextSize(12.5)
                    tv_sub.setSingleLine(True)
                    tv_sub.setEllipsize(TextUtils.TruncateAt.END)

                    title_box.addView(tv_title, LP_Linear(LP_Linear.MATCH_PARENT, LP_Linear.WRAP_CONTENT))
                    title_box.addView(tv_sub, LP_Linear(LP_Linear.MATCH_PARENT, LP_Linear.WRAP_CONTENT))
                    top_bar.addView(title_box, title_lp)

                    root.addView(top_bar, LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.WRAP_CONTENT, Gravity.TOP))

                    bot_bar = LinearLayout(activity)
                    bot_bar.setOrientation(LinearLayout.HORIZONTAL)
                    bot_bar.setGravity(Gravity.CENTER)
                    bot_bar.setBackgroundColor(Color.argb(220, 15, 23, 42))
                    bot_bar.setPadding(14, 12, 14, 12)

                    def make_btn(label_txt, act, bg_rgba):
                        b = Button(activity)
                        b.setText(String(label_txt))
                        b.setTextColor(Color.WHITE)
                        b.setTextSize(13.5)
                        b.setSingleLine(True)
                        b.setFocusable(True)
                        b.setBackgroundColor(bg_rgba)
                        lp = LP_Linear(0, LP_Linear.WRAP_CONTENT, 1.0)
                        lp.leftMargin = 5
                        lp.rightMargin = 5
                        b.setPadding(8, 10, 8, 10)
                        bind_click(b, act)
                        bot_bar.addView(b, lp)
                        return b

                    make_btn("✕ Esci", "close", Color.argb(235, 185, 28, 28))
                    make_btn("⏮ Prec", "prev", Color.argb(220, 30, 41, 59))
                    btn_pp = make_btn("⏸ Pausa", "toggle_play", Color.argb(230, 22, 163, 74))
                    btn_asp = make_btn("⛶ Adatta", "aspect", Color.argb(220, 37, 99, 235))
                    make_btn("⏭ Succ", "next", Color.argb(220, 30, 41, 59))
                    make_btn("↗ Esterno", "external", Color.argb(230, 217, 119, 6))

                    root.addView(bot_bar, LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.WRAP_CONTENT, Gravity.BOTTOM))

                    dismiss_listener = DismissProxy()
                    key_listener = DialogKeyProxy()
                    owner._jni_listeners.extend([dismiss_listener, key_listener])

                    dlg.setContentView(root)
                    dlg.setCancelable(True)
                    dlg.setCanceledOnTouchOutside(False)
                    dlg.setOnDismissListener(dismiss_listener)
                    dlg.setOnKeyListener(key_listener)
                    dlg.show()
                    btn_pp.requestFocus()

                    owner._dialog = dlg
                    owner._title_view = tv_title
                    owner._status_view = tv_sub
                    owner._top_bar = top_bar
                    owner._controls_bar = bot_bar
                    owner._btn_play_pause = btn_pp
                    owner._btn_aspect = btn_asp

                engine_tag = "Media3 ExoPlayer" if owner._using_exo else "Android VideoView"
                if owner._title_view is not None:
                    owner._title_view.setText(String(title))
                if owner._status_view is not None:
                    owner._status_view.setText(String(f"[{engine_tag}] {sub_txt}  •  CH+/CH- Cambia | OK Pausa"))
                if owner._btn_play_pause is not None:
                    owner._btn_play_pause.setText(String("⏸ Pausa"))

                if owner._using_exo and owner._exo is not None:
                    media_item = MediaItem.fromUri(Uri.parse(String(url)))
                    owner._exo.setMediaItem(media_item)
                    owner._exo.prepare()
                    owner._exo.setPlayWhenReady(True)
                elif owner._fallback_vv is not None:
                    owner._fallback_vv.stopPlayback()
                    owner._fallback_vv.setVideoURI(Uri.parse(String(url)))
                    owner._fallback_vv.requestFocus()
                    owner._fallback_vv.start()

            build_or_play()
        except Exception as e:
            if self._on_closed:
                err_s = str(e)
                Clock.schedule_once(lambda *_: self._on_closed(err_s), 0)

    def _set_native_status_direct(self, txt: str):
        if self._status_view is None:
            return
        try:
            from jnius import autoclass

            String = autoclass("java.lang.String")
            self._status_view.setText(String(txt))
        except Exception:
            pass

    def toggle_play_pause(self):
        if platform != "android":
            return
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            String = autoclass("java.lang.String")
            exo = self._exo
            vv = self._fallback_vv
            btn = self._btn_play_pause

            @run_on_ui_thread
            def act():
                if exo is not None:
                    playing = bool(exo.getPlayWhenReady())
                    exo.setPlayWhenReady(not playing)
                    if btn is not None:
                        btn.setText(String("▶ Play" if playing else "⏸ Pausa"))
                    self._set_native_status_direct("In pausa" if playing else "In riproduzione (ExoPlayer)")
                elif vv is not None:
                    if vv.isPlaying():
                        vv.pause()
                        if btn is not None:
                            btn.setText(String("▶ Play"))
                        self._set_native_status_direct("In pausa")
                    else:
                        vv.start()
                        if btn is not None:
                            btn.setText(String("⏸ Pausa"))
                        self._set_native_status_direct("In riproduzione")

            act()
        except Exception:
            pass

    def toggle_aspect(self):
        if platform != "android":
            return
        self._stretch_mode = not self._stretch_mode
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            Gravity = autoclass("android.view.Gravity")
            LP_Frame = autoclass("android.widget.FrameLayout$LayoutParams")
            String = autoclass("java.lang.String")
            target = self._texture_view or self._fallback_vv
            btn = self._btn_aspect
            stretch = self._stretch_mode

            @run_on_ui_thread
            def apply_scale():
                if target is None:
                    return
                if stretch:
                    target.setLayoutParams(LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.MATCH_PARENT, Gravity.FILL))
                    if btn is not None:
                        btn.setText(String("⛶ Normale"))
                    self._set_native_status_direct("Modalità: Adatta schermo")
                else:
                    target.setLayoutParams(LP_Frame(LP_Frame.MATCH_PARENT, LP_Frame.MATCH_PARENT, Gravity.CENTER))
                    if btn is not None:
                        btn.setText(String("⛶ Adatta"))
                    self._set_native_status_direct("Modalità: Proporzioni originali")

            apply_scale()
        except Exception:
            pass

    def toggle_controls_bar(self):
        if platform != "android" or self._controls_bar is None:
            return
        self._controls_visible = not self._controls_visible
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            View = autoclass("android.view.View")
            top_b = self._top_bar
            bot_b = self._controls_bar
            btn_pp = self._btn_play_pause
            vis = View.VISIBLE if self._controls_visible else View.GONE
            is_vis = self._controls_visible

            @run_on_ui_thread
            def set_vis():
                if top_b is not None:
                    top_b.setVisibility(vis)
                if bot_b is not None:
                    bot_b.setVisibility(vis)
                if is_vis and btn_pp is not None:
                    btn_pp.requestFocus()

            set_vis()
        except Exception:
            pass

    def close(self):
        dlg = self._dialog
        exo = self._exo
        vv = self._fallback_vv
        self._closing_guard = True
        self._clear_refs()

        if platform == "android":
            try:
                from android.runnable import run_on_ui_thread

                @run_on_ui_thread
                def teardown():
                    try:
                        if exo is not None:
                            exo.stop()
                            exo.release()
                    except Exception:
                        pass
                    try:
                        if vv is not None:
                            vv.stopPlayback()
                    except Exception:
                        pass
                    try:
                        if dlg is not None:
                            dlg.hide()
                            dlg.dismiss()
                    except Exception:
                        pass
                    self._closing_guard = False

                teardown()
            except Exception:
                self._closing_guard = False
        else:
            self._closing_guard = False

        if self._on_closed:
            cb = self._on_closed
            Clock.schedule_once(lambda *_: cb(None), 0)


def _build_android_intent(url: str, title: str, ua: str = "", referer: str = "", package: str | None = None):
    from jnius import autoclass

    Intent = autoclass("android.content.Intent")
    Uri = autoclass("android.net.Uri")
    String = autoclass("java.lang.String")

    intent = Intent(Intent.ACTION_VIEW)
    uri = Uri.parse(url)
    low_url = url.lower()

    if ".m3u8" in low_url:
        intent.setDataAndType(uri, String("application/vnd.apple.mpegurl"))
    elif ".mpd" in low_url:
        intent.setDataAndType(uri, String("application/dash+xml"))
    else:
        intent.setDataAndType(uri, String("video/*"))

    intent.putExtra(String("title"), String(title))

    if ua:
        intent.putExtra(String("user_agent"), String(ua))
    if referer:
        intent.putExtra(String("referer"), String(referer))

    if package:
        intent.setPackage(String(package))

    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    return intent


def open_stream_with_mode(url, title="IPTV Stream", ua="", referer="", player_mode="Player: Scelta App"):
    if platform != "android":
        import webbrowser

        webbrowser.open(url)
        return True, "Aperto su desktop"

    try:
        from jnius import autoclass, cast

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        String = autoclass("java.lang.String")
        activity = PythonActivity.mActivity

        packages_to_try = PLAYER_PACKAGES.get(player_mode, [])
        for pkg in packages_to_try:
            try:
                pkg_intent = _build_android_intent(url, title, ua=ua, referer=referer, package=pkg)
                activity.startActivity(pkg_intent)
                return True, f"Avviato in {player_mode.replace('Player: ', '')}"
            except Exception:
                continue

        base_intent = _build_android_intent(url, title, ua=ua, referer=referer, package=None)
        chooser = Intent.createChooser(base_intent, cast("java.lang.CharSequence", String("Apri flusso con...")))
        chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(chooser)
        return True, "Selettore player aperto"
    except Exception as e:
        return False, str(e)


def share_text_externally(text, subject="Playlist M3U", saved_path=""):
    if platform == "android":
        if len(text) > 120000:
            return True, f"Salvata in {saved_path or 'cartella app'} (lista estesa)"
        try:
            from jnius import autoclass, cast

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")

            send_intent = Intent(Intent.ACTION_SEND)
            send_intent.setType(String("text/plain"))
            send_intent.putExtra(Intent.EXTRA_SUBJECT, cast("java.lang.CharSequence", String(subject)))
            send_intent.putExtra(Intent.EXTRA_TEXT, cast("java.lang.CharSequence", String(text)))

            chooser = Intent.createChooser(send_intent, cast("java.lang.CharSequence", String("Condividi / Salva M3U")))
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            PythonActivity.mActivity.startActivity(chooser)
            return True, ""
        except Exception as e:
            return False, str(e)
    else:
        Clipboard.copy(text)
        return True, "Copiata negli appunti"


class TVButton(Button):
    """Pulsante con evidenziazione ad alto contrasto per navigazione con telecomando D-Pad."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.is_focusable = True
        with self.canvas.after:
            self._focus_color = KivyColor(0.98, 0.8, 0.08, 0)
            self._focus_line = Line(rectangle=(self.x, self.y, self.width, self.height), width=dp(2.2))
        self.bind(pos=self._update_focus_rect, size=self._update_focus_rect, state=self._on_state_visual)

    def _update_focus_rect(self, *_):
        self._focus_line.rectangle = (self.x + 1, self.y + 1, max(0, self.width - 2), max(0, self.height - 2))

    def _on_state_visual(self, _inst, value):
        self._focus_color.a = 1.0 if value == "down" else 0.0

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        if keycode[1] in ("enter", "numpadenter", "spacebar"):
            self.trigger_action(duration=0.05)
            return True
        return super().keyboard_on_key_down(window, keycode, text, modifiers)


class ChannelRow(RecycleDataViewBehavior, FocusBehavior, BoxLayout):
    index = NumericProperty(0)
    uid = NumericProperty(0)
    name = StringProperty("")
    group = StringProperty("")
    epg_now = StringProperty("")
    epg_next = StringProperty("")
    status = StringProperty("PENDING")
    latency = StringProperty("-")
    detail = StringProperty("")
    url = StringProperty("")
    favorite = BooleanProperty(False)
    device_profile = StringProperty(DEFAULT_DEVICE_PROFILE)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        cfg = get_profile_config(self.device_profile)
        self.orientation = "horizontal"
        self.padding = (dp(cfg["pad"]), dp(cfg["gap"]))
        self.spacing = dp(cfg["gap"])
        self.is_focusable = True

        with self.canvas.after:
            self._row_focus_color = KivyColor(0.98, 0.8, 0.08, 0)
            self._row_focus_line = Line(rectangle=(self.x, self.y, self.width, self.height), width=dp(2.4))
        self.bind(pos=self._update_row_focus, size=self._update_row_focus)

        self.fav_btn = TVButton(
            text="☆",
            size_hint_x=None,
            width=dp(38),
            background_normal="",
            background_color=(0.15, 0.21, 0.33, 1),
            font_size=scaled_sp(15, self.device_profile),
        )
        self.fav_btn.bind(on_release=self._on_toggle_fav)
        self.add_widget(self.fav_btn)

        info_box = BoxLayout(orientation="vertical", spacing=dp(3))
        self.title_lbl = Label(
            text="",
            halign="left",
            valign="middle",
            bold=True,
            font_size=scaled_sp(13, self.device_profile),
            shorten=True,
            shorten_from="right",
            color=(0.96, 0.97, 0.99, 1),
        )
        self.title_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))

        self.sub_lbl = Label(
            text="",
            halign="left",
            valign="middle",
            font_size=scaled_sp(11, self.device_profile),
            shorten=True,
            shorten_from="right",
            color=(0.65, 0.72, 0.82, 1),
        )
        self.sub_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))

        self.epg_lbl = Label(
            text="",
            halign="left",
            valign="middle",
            font_size=scaled_sp(11, self.device_profile),
            shorten=True,
            shorten_from="right",
            color=(0.45, 0.82, 0.98, 1),
            size_hint_y=None,
            height=0,
        )
        self.epg_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))

        info_box.add_widget(self.title_lbl)
        info_box.add_widget(self.sub_lbl)
        info_box.add_widget(self.epg_lbl)
        self.add_widget(info_box)

        self.info_btn = TVButton(
            text="ℹ",
            size_hint_x=None,
            width=dp(38),
            background_normal="",
            background_color=(0.18, 0.26, 0.42, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.info_btn.bind(on_release=self._on_info)
        self.add_widget(self.info_btn)

        self.play_btn = TVButton(
            text="▶ PLAY",
            size_hint_x=None,
            width=dp(76),
            bold=True,
            background_normal="",
            background_color=(0.09, 0.64, 0.29, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.play_btn.bind(on_release=self._on_play)
        self.add_widget(self.play_btn)

    def _update_row_focus(self, *_):
        self._row_focus_line.rectangle = (self.x + 1, self.y + 1, max(0, self.width - 2), max(0, self.height - 2))

    def _apply_profile_to_row(self, profile_name: str):
        cfg = get_profile_config(profile_name)
        wide = is_wide_layout(profile_name)
        self.padding = (dp(12 if wide else cfg["pad"]), dp(8 if wide else cfg["gap"]))
        self.spacing = dp(8 if wide else cfg["gap"])
        self.fav_btn.width = dp(48 if wide else 38)
        self.fav_btn.font_size = scaled_sp(16 if wide else 15, profile_name)
        self.info_btn.width = dp(48 if wide else 38)
        self.info_btn.font_size = scaled_sp(14 if wide else 13, profile_name)
        self.play_btn.width = dp(100 if wide else 76)
        self.play_btn.font_size = scaled_sp(12.5 if wide else 12, profile_name)
        self.title_lbl.font_size = scaled_sp(13.5 if wide else 13, profile_name)
        self.sub_lbl.font_size = scaled_sp(11.5 if wide else 11, profile_name)
        self.epg_lbl.font_size = scaled_sp(11.5 if wide else 11, profile_name)
        if self.epg_lbl.text:
            self.epg_lbl.height = dp(cfg["epg_h"])

    def on_focus(self, instance, value):
        self._row_focus_color.a = 1.0 if value else 0.0
        if value:
            self.play_btn.background_color = (0.14, 0.8, 0.37, 1)
        else:
            self.play_btn.background_color = (0.09, 0.64, 0.29, 1)

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        if keycode[1] in ("enter", "numpadenter", "spacebar"):
            self._on_play()
            return True
        return super().keyboard_on_key_down(window, keycode, text, modifiers)

    def refresh_view_attrs(self, rv, index, data):
        self.index = index
        self.uid = data["uid"]
        self.name = data["name"]
        self.group = data.get("group", "")
        self.epg_now = data.get("epg_now", "")
        self.epg_next = data.get("epg_next", "")
        self.status = data["status"]
        self.latency = data["latency"]
        self.detail = data["detail"]
        self.url = data["url"]
        self.favorite = data.get("favorite", False)
        prof = data.get("device_profile", DEFAULT_DEVICE_PROFILE)
        self.device_profile = prof
        cfg = get_profile_config(prof)

        self.fav_btn.text = "★" if self.favorite else "☆"
        self.fav_btn.color = (0.98, 0.8, 0.08, 1) if self.favorite else (0.7, 0.75, 0.85, 1)

        if self.status == "ONLINE":
            badge = "[ONLINE]"
            self.title_lbl.color = (0.29, 0.87, 0.5, 1)
        elif self.status == "OFFLINE":
            badge = "[OFFLINE]"
            self.title_lbl.color = (0.97, 0.44, 0.44, 1)
        else:
            badge = "[ATTESA]"
            self.title_lbl.color = (0.95, 0.96, 0.98, 1)

        grp_part = f" • {self.group}" if self.group else ""
        self.title_lbl.text = f"{badge} {self.name}"
        self.sub_lbl.text = f"{self.detail} ({self.latency}){grp_part}"

        if self.epg_now or self.epg_next:
            nxt_txt = f"  |  Poi: {self.epg_next}" if self.epg_next else ""
            now_txt = self.epg_now or "Nessun programma attivo"
            self.epg_lbl.text = f"📺 Ora: {now_txt}{nxt_txt}"
            self.epg_lbl.height = dp(cfg["epg_h"])
        else:
            self.epg_lbl.text = ""
            self.epg_lbl.height = 0

        self._apply_profile_to_row(prof)
        return super().refresh_view_attrs(rv, index, data)

    def _on_toggle_fav(self, *_):
        app = App.get_running_app()
        if app:
            app.toggle_favorite(self.uid)

    def _on_info(self, *_):
        app = App.get_running_app()
        if app:
            app.open_channel_details(self.uid)

    def _on_play(self, *_):
        app = App.get_running_app()
        if app:
            app.play_channel(self.uid)


class LoginScreen(AnchorLayout):
    """Schermata di accesso centrata e adattiva per Box TV, Tablet e Telefonino."""

    def __init__(self, on_authenticated, initial_profile=DEFAULT_DEVICE_PROFILE, on_profile_change=None, **kwargs):
        super().__init__(**kwargs)
        self.anchor_x = "center"
        self.anchor_y = "center"
        self.on_authenticated = on_authenticated
        self.on_profile_change = on_profile_change
        self.login_mode = "APP"
        self.device_profile = initial_profile if initial_profile in DEVICE_PROFILES else DEFAULT_DEVICE_PROFILE

        saved = self._load_auth()
        cfg = get_profile_config(self.device_profile)
        wide = is_wide_layout(self.device_profile)

        self.padding = dp(cfg["login_pad"])

        self.scroll = ScrollView(
            size_hint=(None if wide else 1, 1),
            width=min(dp(640), max(dp(320), Window.width - dp(40))) if wide else Window.width,
        )
        Window.bind(size=self._on_win_resize)

        self.card = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(cfg["gap"] + 4),
            padding=(dp(16 if wide else 6), dp(14 if wide else 8)),
        )
        self.card.bind(minimum_height=self.card.setter("height"))

        self.logo_lbl = Label(
            text="📺 IPTV STUDIO PREMIUM",
            size_hint_y=None,
            height=dp(44 if wide else 36),
            bold=True,
            font_size=scaled_sp(20, self.device_profile),
            color=(0.98, 0.78, 0.22, 1),
        )
        self.sub_lbl = Label(
            text="Seleziona il dispositivo e accedi",
            size_hint_y=None,
            height=dp(26 if wide else 22),
            font_size=scaled_sp(12, self.device_profile),
            color=(0.65, 0.75, 0.88, 1),
        )
        self.card.add_widget(self.logo_lbl)
        self.card.add_widget(self.sub_lbl)

        dev_row = BoxLayout(size_hint_y=None, height=dp(cfg["ctrl_row_h"]), spacing=dp(8))
        self.btn_dev_tv = TVButton(
            text="📺 Box TV",
            bold=True,
            background_normal="",
            background_color=(0.13, 0.34, 0.52, 1) if self.device_profile == "Layout: Box TV" else (0.18, 0.24, 0.36, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.btn_dev_tablet = TVButton(
            text="📟 Tablet",
            bold=True,
            background_normal="",
            background_color=(0.13, 0.34, 0.52, 1) if self.device_profile == "Layout: Tablet" else (0.18, 0.24, 0.36, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.btn_dev_phone = TVButton(
            text="📱 Telefono",
            bold=True,
            background_normal="",
            background_color=(0.13, 0.34, 0.52, 1) if self.device_profile == "Layout: Telefono" else (0.18, 0.24, 0.36, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.btn_dev_tv.bind(on_release=lambda *_: self._set_device_profile("Layout: Box TV"))
        self.btn_dev_tablet.bind(on_release=lambda *_: self._set_device_profile("Layout: Tablet"))
        self.btn_dev_phone.bind(on_release=lambda *_: self._set_device_profile("Layout: Telefono"))
        dev_row.add_widget(self.btn_dev_tv)
        dev_row.add_widget(self.btn_dev_tablet)
        dev_row.add_widget(self.btn_dev_phone)
        self.card.add_widget(dev_row)

        mode_row = BoxLayout(size_hint_y=None, height=dp(cfg["ctrl_row_h"]), spacing=dp(8))
        self.btn_mode_app = TVButton(
            text="Account App",
            bold=True,
            background_normal="",
            background_color=(0.15, 0.39, 0.92, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_mode_xtream = TVButton(
            text="Xtream Codes API",
            bold=True,
            background_normal="",
            background_color=(0.18, 0.24, 0.36, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_mode_app.bind(on_release=lambda *_: self._set_mode("APP"))
        self.btn_mode_xtream.bind(on_release=lambda *_: self._set_mode("XTREAM"))
        mode_row.add_widget(self.btn_mode_app)
        mode_row.add_widget(self.btn_mode_xtream)
        self.card.add_widget(mode_row)

        self.server_input = TextInput(
            text=saved.get("xtream_server", ""),
            multiline=False,
            size_hint_y=None,
            height=0,
            opacity=0,
            disabled=True,
            hint_text="Server URL (es. http://tv.esempio.it:8080)",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            cursor_color=(0.98, 0.78, 0.22, 1),
            font_size=scaled_sp(13, self.device_profile),
            padding=(dp(12), dp(11)),
        )
        self.card.add_widget(self.server_input)

        self.user_input = TextInput(
            text=saved.get("username", DEFAULT_APP_USER),
            multiline=False,
            size_hint_y=None,
            height=dp(cfg["input_h"]),
            hint_text="Username (default: admin)",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            cursor_color=(0.98, 0.78, 0.22, 1),
            font_size=scaled_sp(14, self.device_profile),
            padding=(dp(12), dp(12)),
        )
        self.card.add_widget(self.user_input)

        pass_row = BoxLayout(size_hint_y=None, height=dp(cfg["input_h"]), spacing=dp(8))
        self.pass_input = TextInput(
            text=saved.get("password", DEFAULT_APP_PASS),
            multiline=False,
            password=True,
            hint_text="Password (default: 1234)",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            cursor_color=(0.98, 0.78, 0.22, 1),
            font_size=scaled_sp(14, self.device_profile),
            padding=(dp(12), dp(12)),
        )
        pass_row.add_widget(self.pass_input)

        self.btn_eye = TVButton(
            text="👁",
            size_hint_x=None,
            width=dp(64 if wide else 50),
            background_normal="",
            background_color=(0.18, 0.25, 0.38, 1),
            font_size=scaled_sp(15, self.device_profile),
        )
        self.btn_eye.bind(on_release=self._toggle_pass)
        pass_row.add_widget(self.btn_eye)
        self.card.add_widget(pass_row)

        rem_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.chk_remember = CheckBox(active=saved.get("remember", True), size_hint_x=None, width=dp(36))
        self.rem_lbl = Label(
            text="Ricorda credenziali al prossimo avvio",
            halign="left",
            valign="middle",
            font_size=scaled_sp(12, self.device_profile),
            color=(0.8, 0.86, 0.94, 1),
        )
        self.rem_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        rem_row.add_widget(self.chk_remember)
        rem_row.add_widget(self.rem_lbl)
        self.card.add_widget(rem_row)

        self.err_lbl = Label(
            text="",
            size_hint_y=None,
            height=dp(26),
            font_size=scaled_sp(12, self.device_profile),
            bold=True,
            color=(0.98, 0.45, 0.45, 1),
        )
        self.card.add_widget(self.err_lbl)

        self.btn_login = TVButton(
            text="🔓 ACCEDI A IPTV STUDIO",
            size_hint_y=None,
            height=dp(cfg["action_h"]),
            bold=True,
            background_normal="",
            background_color=(0.09, 0.64, 0.29, 1),
            font_size=scaled_sp(14, self.device_profile),
        )
        self.btn_login.bind(on_release=lambda *_: self._submit_login())
        self.card.add_widget(self.btn_login)

        self.hint_lbl = Label(
            text="Telecomando TV: Frecce + OK per accedere (admin / 1234)",
            size_hint_y=None,
            height=dp(24),
            font_size=scaled_sp(11, self.device_profile),
            color=(0.55, 0.65, 0.78, 1),
        )
        self.card.add_widget(self.hint_lbl)

        self.scroll.add_widget(self.card)
        self.add_widget(self.scroll)

        if saved.get("mode") == "XTREAM":
            self._set_mode("XTREAM")

        Clock.schedule_once(lambda *_: setattr(self.btn_login, "focus", True), 0.15)

    def _on_win_resize(self, _win, size):
        if is_wide_layout(self.device_profile):
            self.scroll.size_hint_x = None
            self.scroll.width = min(dp(640), max(dp(320), size[0] - dp(40)))

    def _set_device_profile(self, profile_name: str):
        if profile_name not in DEVICE_PROFILES:
            return
        self.device_profile = profile_name
        cfg = get_profile_config(profile_name)
        wide = is_wide_layout(profile_name)

        self.padding = dp(cfg["login_pad"])
        if wide:
            self.scroll.size_hint_x = None
            self.scroll.width = min(dp(640), max(dp(320), Window.width - dp(40)))
        else:
            self.scroll.size_hint_x = 1

        self.card.spacing = dp(cfg["gap"] + 4)
        self.card.padding = (dp(16 if wide else 6), dp(14 if wide else 8))

        self.btn_dev_tv.background_color = (0.13, 0.34, 0.52, 1) if profile_name == "Layout: Box TV" else (0.18, 0.24, 0.36, 1)
        self.btn_dev_tablet.background_color = (0.13, 0.34, 0.52, 1) if profile_name == "Layout: Tablet" else (0.18, 0.24, 0.36, 1)
        self.btn_dev_phone.background_color = (0.13, 0.34, 0.52, 1) if profile_name == "Layout: Telefono" else (0.18, 0.24, 0.36, 1)

        self.logo_lbl.font_size = scaled_sp(20, profile_name)
        self.sub_lbl.font_size = scaled_sp(12, profile_name)
        self.btn_dev_tv.font_size = scaled_sp(12, profile_name)
        self.btn_dev_tablet.font_size = scaled_sp(12, profile_name)
        self.btn_dev_phone.font_size = scaled_sp(12, profile_name)
        self.btn_mode_app.font_size = scaled_sp(13, profile_name)
        self.btn_mode_xtream.font_size = scaled_sp(13, profile_name)

        self.user_input.height = dp(cfg["input_h"])
        self.user_input.font_size = scaled_sp(14, profile_name)
        self.pass_input.parent.height = dp(cfg["input_h"])
        self.pass_input.font_size = scaled_sp(14, profile_name)
        self.btn_eye.width = dp(64 if wide else 50)
        self.btn_eye.font_size = scaled_sp(15, profile_name)

        if self.login_mode == "XTREAM":
            self.server_input.height = dp(cfg["input_h"])
        self.server_input.font_size = scaled_sp(13, profile_name)

        self.rem_lbl.font_size = scaled_sp(12, profile_name)
        self.err_lbl.font_size = scaled_sp(12, profile_name)
        self.btn_login.height = dp(cfg["action_h"])
        self.btn_login.font_size = scaled_sp(14, profile_name)
        self.hint_lbl.font_size = scaled_sp(11, profile_name)

        if self.on_profile_change:
            self.on_profile_change(profile_name)

    def _auth_path(self):
        app = App.get_running_app()
        base_dir = app.user_data_dir if app else "."
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, "auth.json")

    def _load_auth(self):
        try:
            p = self._auth_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_auth(self, payload):
        try:
            with open(self._auth_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _set_mode(self, mode):
        self.login_mode = mode
        self.err_lbl.text = ""
        cfg = get_profile_config(self.device_profile)
        if mode == "XTREAM":
            self.btn_mode_xtream.background_color = (0.15, 0.39, 0.92, 1)
            self.btn_mode_app.background_color = (0.18, 0.24, 0.36, 1)
            self.server_input.height = dp(cfg["input_h"])
            self.server_input.opacity = 1
            self.server_input.disabled = False
            self.user_input.hint_text = "Username Xtream"
            self.pass_input.hint_text = "Password Xtream"
        else:
            self.btn_mode_app.background_color = (0.15, 0.39, 0.92, 1)
            self.btn_mode_xtream.background_color = (0.18, 0.24, 0.36, 1)
            self.server_input.height = 0
            self.server_input.opacity = 0
            self.server_input.disabled = True
            self.user_input.hint_text = "Username (default: admin)"
            self.pass_input.hint_text = "Password (default: 1234)"

    def _toggle_pass(self, *_):
        self.pass_input.password = not self.pass_input.password

    def _submit_login(self):
        user = self.user_input.text.strip()
        pwd = self.pass_input.text.strip()
        srv = self.server_input.text.strip().rstrip("/")

        if not user or not pwd:
            self.err_lbl.text = "Inserisci username e password."
            return

        playlist_url = None
        epg_url = None

        if self.login_mode == "XTREAM":
            if not srv.lower().startswith(("http://", "https://")):
                self.err_lbl.text = "Inserisci un Server URL valido (http://...)."
                return
            u_enc = urllib.parse.quote(user)
            p_enc = urllib.parse.quote(pwd)
            playlist_url = f"{srv}/get.php?username={u_enc}&password={p_enc}&type=m3u_plus&output=ts"
            epg_url = f"{srv}/xmltv.php?username={u_enc}&password={p_enc}"
        else:
            saved = self._load_auth()
            expected_user = saved.get("app_user", DEFAULT_APP_USER)
            expected_pass = saved.get("app_pass", DEFAULT_APP_PASS)
            if user != expected_user or pwd != expected_pass:
                self.err_lbl.text = "Credenziali non valide (default: admin / 1234)."
                return

        if self.chk_remember.active:
            self._save_auth(
                {
                    "mode": self.login_mode,
                    "username": user,
                    "password": pwd,
                    "xtream_server": srv,
                    "remember": True,
                    "app_user": DEFAULT_APP_USER,
                    "app_pass": DEFAULT_APP_PASS,
                }
            )
        else:
            self._save_auth({"remember": False})

        self.on_authenticated(
            username=user,
            playlist_url=playlist_url,
            epg_url=epg_url,
            device_profile=self.device_profile,
        )


class IPTVStudioPremiumRoot(BoxLayout):
    stats_text = StringProperty("Totale: 0 | Online: 0 | Offline: 0")
    status_line = StringProperty("IPTV Studio Premium (Box TV / ExoPlayer) pronto.")
    rv_data = ListProperty([])

    def __init__(
        self,
        username="admin",
        initial_playlist_url=None,
        initial_epg_url=None,
        initial_device_profile=DEFAULT_DEVICE_PROFILE,
        on_logout=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.username = username
        self.on_logout = on_logout
        self.channels = []
        self.uid_seq = 0
        self.ssl_ctx = make_ssl_context()
        self.is_checking = False
        self.stop_flag = False
        self.filter_mode = "ALL"
        self.current_channel = None
        self.epg_by_id = {}
        self.epg_by_norm = {}
        self.active_popup = None
        self._search_debounce_ev = None

        prefs = self.load_preferences()
        self.selected_player_mode = prefs.get("player_mode", "Player: ExoPlayer")
        self.device_profile = (
            initial_device_profile
            if initial_device_profile in DEVICE_PROFILES
            else prefs.get("device_profile", DEFAULT_DEVICE_PROFILE)
        )
        self.save_preferences(player_mode=self.selected_player_mode, device_profile=self.device_profile)
        self.url_history = self.load_url_history()

        self.player_window = AndroidExoPlayerWindow(
            on_prev=self.play_prev_channel,
            on_next=self.play_next_channel,
            on_external=lambda: self.open_current_externally(mode="Player: Scelta App"),
            on_closed=self._on_player_window_closed,
        )

        self._build_ui()
        if initial_playlist_url:
            self.url_input.text = initial_playlist_url
        Window.bind(on_keyboard=self._on_window_key)
        Clock.schedule_once(lambda *_: self.load_from_url(), 0.25)
        if initial_epg_url:
            Clock.schedule_once(lambda *_: self.load_epg_from_url(initial_epg_url), 1.2)

    def _on_window_key(self, window, key, scancode, codepoint, modifiers):
        if key == 27:
            if self.player_window._dialog is not None:
                self.player_window.close()
                return True
            if self.active_popup is not None:
                self.active_popup.dismiss()
                self.active_popup = None
                return True
        return False

    def _data_dir(self):
        app = App.get_running_app()
        base_dir = app.user_data_dir if app else "."
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    def _fav_file_path(self):
        return os.path.join(self._data_dir(), "favorites.json")

    def _settings_file_path(self):
        return os.path.join(self._data_dir(), "settings.json")

    def _history_file_path(self):
        return os.path.join(self._data_dir(), "history.json")

    def load_preferences(self):
        out = {
            "player_mode": "Player: ExoPlayer",
            "device_profile": DEFAULT_DEVICE_PROFILE,
        }
        try:
            path = self._settings_file_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("player_mode") in PLAYER_OPTIONS:
                    out["player_mode"] = data["player_mode"]
                if data.get("device_profile") in DEVICE_PROFILES:
                    out["device_profile"] = data["device_profile"]
        except Exception:
            pass
        return out

    def save_preferences(self, player_mode=None, device_profile=None):
        try:
            cur = self.load_preferences()
            if player_mode in PLAYER_OPTIONS:
                cur["player_mode"] = player_mode
            if device_profile in DEVICE_PROFILES:
                cur["device_profile"] = device_profile
            with open(self._settings_file_path(), "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_url_history(self):
        try:
            path = self._history_file_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [str(x) for x in data if isinstance(x, str) and x.strip()][:15]
        except Exception:
            pass
        return []

    def record_url_history(self, url: str):
        clean = (url or "").strip()
        if not clean.lower().startswith(("http://", "https://")):
            return
        updated = [clean] + [u for u in self.url_history if u != clean]
        self.url_history = updated[:15]
        try:
            with open(self._history_file_path(), "w", encoding="utf-8") as f:
                json.dump(self.url_history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_saved_favorites(self):
        try:
            path = self._fav_file_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
        return set()

    def save_favorites(self):
        try:
            fav_urls = [c["url"] for c in self.channels if c.get("favorite")]
            with open(self._fav_file_path(), "w", encoding="utf-8") as f:
                json.dump(fav_urls, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _schedule_search_refresh(self, *_):
        if self._search_debounce_ev is not None:
            self._search_debounce_ev.cancel()
        self._search_debounce_ev = Clock.schedule_once(lambda *_: self.refresh_view(), 0.12)

    def _build_ui(self):
        cfg = get_profile_config(self.device_profile)
        wide = is_wide_layout(self.device_profile)
        self.orientation = "vertical"
        self.padding = dp(cfg["pad"])
        self.spacing = dp(cfg["gap"])

        self.top_hdr = BoxLayout(size_hint_y=None, height=dp(cfg["hdr_h"]), spacing=dp(cfg["gap"]))
        self.header = Label(
            text=f"IPTV Studio • {self.username}",
            halign="left",
            valign="middle",
            bold=True,
            font_size=scaled_sp(15, self.device_profile),
            shorten=True,
            shorten_from="right",
            color=(0.98, 0.78, 0.22, 1),
        )
        self.header.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        self.top_hdr.add_widget(self.header)

        self.device_spinner = Spinner(
            text=self.device_profile,
            values=list(DEVICE_PROFILES.keys()),
            size_hint_x=None,
            width=dp(164 if wide else 136),
            background_normal="",
            background_color=(0.24, 0.18, 0.45, 1),
            font_size=scaled_sp(11, self.device_profile),
        )
        self.device_spinner.bind(text=self._on_device_profile_selected)
        self.top_hdr.add_widget(self.device_spinner)

        self.btn_logout = TVButton(
            text="Esci",
            size_hint_x=None,
            width=dp(76 if wide else 58),
            background_normal="",
            background_color=(0.55, 0.16, 0.2, 1),
            font_size=scaled_sp(11, self.device_profile),
            bold=True,
        )
        self.btn_logout.bind(on_release=lambda *_: self._do_logout())
        self.top_hdr.add_widget(self.btn_logout)
        self.add_widget(self.top_hdr)

        self.preset_spinner = Spinner(
            text="Seca (Italia MIX)",
            values=list(PRESETS.keys()),
            background_normal="",
            background_color=(0.15, 0.22, 0.36, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.preset_spinner.bind(text=self._on_preset_selected)

        self.player_spinner = Spinner(
            text=self.selected_player_mode,
            values=PLAYER_OPTIONS,
            size_hint_x=None,
            width=dp(cfg["player_w"]),
            background_normal="",
            background_color=(0.13, 0.34, 0.52, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.player_spinner.bind(text=self._on_player_mode_selected)

        self.btn_paste = TVButton(
            text="Liste",
            size_hint_x=None,
            width=dp(cfg["btn_w_sm"]),
            background_normal="",
            background_color=(0.2, 0.27, 0.4, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        self.btn_paste.bind(on_release=lambda *_: self.open_lists_popup())

        self.btn_epg = TVButton(
            text="📺 EPG",
            size_hint_x=None,
            width=dp(cfg["btn_w_sm"] + 14),
            background_normal="",
            background_color=(0.16, 0.45, 0.68, 1),
            font_size=scaled_sp(12, self.device_profile),
            bold=True,
        )
        self.btn_epg.bind(on_release=lambda *_: self.open_epg_popup())

        self.url_input = TextInput(
            text=PRESETS["Seca (Italia MIX)"],
            multiline=False,
            hint_text="URL playlist .m3u / .m3u8...",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            cursor_color=(0.98, 0.78, 0.22, 1),
            font_size=scaled_sp(13, self.device_profile),
            padding=(dp(8), dp(10)),
        )

        self.btn_load = TVButton(
            text="Carica",
            size_hint_x=None,
            width=dp(cfg["btn_w_md"] + 10),
            bold=True,
            background_normal="",
            background_color=(0.15, 0.39, 0.92, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_load.bind(on_release=lambda *_: self.load_from_url())

        self.btn_check = TVButton(
            text="⚡ Controlla",
            bold=True,
            background_normal="",
            background_color=(0.09, 0.64, 0.29, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_check.bind(on_release=lambda *_: self.start_check())

        self.btn_stop = TVButton(
            text="⏹ Stop",
            size_hint_x=None,
            width=dp(cfg["btn_w_md"]),
            disabled=True,
            background_normal="",
            background_color=(0.86, 0.15, 0.15, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_stop.bind(on_release=lambda *_: self.stop_check())

        self.btn_export = TVButton(
            text="💾 Esporta",
            size_hint_x=None,
            width=dp(cfg["btn_w_lg"]),
            background_normal="",
            background_color=(0.85, 0.47, 0.02, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        self.btn_export.bind(on_release=lambda *_: self.export_online_m3u())

        self.search_input = TextInput(
            multiline=False,
            hint_text="🔍 Cerca canale, gruppo o EPG...",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            font_size=scaled_sp(13, self.device_profile),
            padding=(dp(8), dp(9)),
        )
        self.search_input.bind(text=self._schedule_search_refresh)

        self.filter_btns = {}
        for label, mode in [("Tutti", "ALL"), ("Online", "ONLINE"), ("Offline", "OFFLINE"), ("★", "FAV")]:
            b = TVButton(
                text=label,
                size_hint_x=None,
                width=dp(54 if mode != "FAV" else 38),
                background_normal="",
                background_color=(0.15, 0.39, 0.92, 1) if mode == "ALL" else (0.18, 0.24, 0.36, 1),
                font_size=scaled_sp(12, self.device_profile),
            )
            b.bind(on_release=lambda inst, m=mode: self.set_filter(m))
            self.filter_btns[mode] = b

        self.toolbar_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(cfg["gap"]))
        self.add_widget(self.toolbar_box)

        self.stats_lbl = Label(
            text=self.stats_text,
            size_hint_y=None,
            height=dp(26 if wide else 24),
            font_size=scaled_sp(12, self.device_profile),
            bold=True,
            shorten=True,
            shorten_from="right",
            color=(0.82, 0.87, 0.95, 1),
        )
        self.stats_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        self.bind(stats_text=self.stats_lbl.setter("text"))
        self.add_widget(self.stats_lbl)

        self.rv = RecycleView(size_hint=(1, 1))
        self.rv_layout = RecycleGridLayout(
            cols=cfg["cols"],
            default_size=(None, dp(cfg["row_h"])),
            default_size_hint=(1, None),
            size_hint_y=None,
            spacing=dp(cfg["gap"]),
        )
        self.rv_layout.bind(minimum_height=self.rv_layout.setter("height"))
        self.rv.add_widget(self.rv_layout)
        self.rv.viewclass = ChannelRow
        self.bind(rv_data=self.rv.setter("data"))
        self.add_widget(self.rv)

        self.status_lbl = Label(
            text=self.status_line,
            size_hint_y=None,
            height=dp(26 if wide else 24),
            font_size=scaled_sp(11, self.device_profile),
            shorten=True,
            shorten_from="right",
            color=(0.6, 0.68, 0.8, 1),
        )
        self.status_lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        self.bind(status_line=self.status_lbl.setter("text"))
        self.add_widget(self.status_lbl)

        self._rebuild_toolbar()

    def _detach_all_toolbar_widgets(self):
        widgets = [
            self.preset_spinner,
            self.player_spinner,
            self.btn_paste,
            self.btn_epg,
            self.url_input,
            self.btn_load,
            self.btn_check,
            self.btn_stop,
            self.btn_export,
            self.search_input,
            *self.filter_btns.values(),
        ]
        for w in widgets:
            if w.parent is not None:
                w.parent.remove_widget(w)
        self.toolbar_box.clear_widgets()

    def _rebuild_toolbar(self):
        cfg = get_profile_config(self.device_profile)
        wide = is_wide_layout(self.device_profile)
        row_h = dp(cfg["ctrl_row_h"])
        gap = dp(cfg["gap"])

        self._detach_all_toolbar_widgets()
        self.toolbar_box.spacing = gap

        if wide:
            self.btn_check.size_hint_x = None
            self.btn_check.width = dp(144 if self.device_profile == "Layout: Box TV" else 136)

            r1 = BoxLayout(size_hint_y=None, height=row_h, spacing=gap)
            r1.add_widget(self.preset_spinner)
            r1.add_widget(self.player_spinner)
            r1.add_widget(self.btn_paste)
            r1.add_widget(self.btn_epg)
            r1.add_widget(self.btn_check)
            r1.add_widget(self.btn_stop)
            r1.add_widget(self.btn_export)

            r2 = BoxLayout(size_hint_y=None, height=row_h, spacing=gap)
            self.url_input.size_hint_x = 0.54
            self.search_input.size_hint_x = 0.46
            r2.add_widget(self.url_input)
            r2.add_widget(self.btn_load)
            r2.add_widget(self.search_input)
            for mode in ("ALL", "ONLINE", "OFFLINE", "FAV"):
                r2.add_widget(self.filter_btns[mode])

            self.toolbar_box.add_widget(r1)
            self.toolbar_box.add_widget(r2)
            self.toolbar_box.height = row_h * 2 + gap
        else:
            self.btn_check.size_hint_x = 1
            self.url_input.size_hint_x = 1
            self.search_input.size_hint_x = 1

            r1 = BoxLayout(size_hint_y=None, height=row_h, spacing=gap)
            r1.add_widget(self.preset_spinner)
            r1.add_widget(self.player_spinner)
            r1.add_widget(self.btn_paste)
            r1.add_widget(self.btn_epg)

            r2 = BoxLayout(size_hint_y=None, height=row_h, spacing=gap)
            r2.add_widget(self.url_input)
            r2.add_widget(self.btn_load)

            r3 = BoxLayout(size_hint_y=None, height=row_h, spacing=gap)
            r3.add_widget(self.btn_check)
            r3.add_widget(self.btn_stop)
            r3.add_widget(self.btn_export)

            r4 = BoxLayout(size_hint_y=None, height=row_h, spacing=dp(4))
            r4.add_widget(self.search_input)
            for mode in ("ALL", "ONLINE", "OFFLINE", "FAV"):
                r4.add_widget(self.filter_btns[mode])

            self.toolbar_box.add_widget(r1)
            self.toolbar_box.add_widget(r2)
            self.toolbar_box.add_widget(r3)
            self.toolbar_box.add_widget(r4)
            self.toolbar_box.height = row_h * 4 + gap * 3

    def apply_device_profile(self, profile_name: str):
        if profile_name not in DEVICE_PROFILES:
            return
        self.device_profile = profile_name
        cfg = get_profile_config(profile_name)
        wide = is_wide_layout(profile_name)

        self.padding = dp(cfg["pad"])
        self.spacing = dp(cfg["gap"])

        self.top_hdr.height = dp(cfg["hdr_h"])
        self.top_hdr.spacing = dp(cfg["gap"])
        self.header.font_size = scaled_sp(15, profile_name)
        self.device_spinner.width = dp(164 if wide else 136)
        self.device_spinner.font_size = scaled_sp(11, profile_name)
        self.btn_logout.width = dp(76 if wide else 58)
        self.btn_logout.font_size = scaled_sp(11, profile_name)

        self.preset_spinner.font_size = scaled_sp(12, profile_name)
        self.player_spinner.width = dp(cfg["player_w"])
        self.player_spinner.font_size = scaled_sp(12, profile_name)
        self.btn_paste.width = dp(cfg["btn_w_sm"])
        self.btn_paste.font_size = scaled_sp(12, profile_name)
        self.btn_epg.width = dp(cfg["btn_w_sm"] + 14)
        self.btn_epg.font_size = scaled_sp(12, profile_name)

        self.url_input.font_size = scaled_sp(13, profile_name)
        self.btn_load.width = dp(cfg["btn_w_md"] + 10)
        self.btn_load.font_size = scaled_sp(13, profile_name)

        self.btn_check.font_size = scaled_sp(13, profile_name)
        self.btn_stop.width = dp(cfg["btn_w_md"])
        self.btn_stop.font_size = scaled_sp(13, profile_name)
        self.btn_export.width = dp(cfg["btn_w_lg"])
        self.btn_export.font_size = scaled_sp(13, profile_name)

        self.search_input.font_size = scaled_sp(13, profile_name)
        for mode, btn in self.filter_btns.items():
            base_w = 54 if mode != "FAV" else 38
            btn.width = dp(base_w + (18 if wide else 0))
            btn.font_size = scaled_sp(12, profile_name)

        self._rebuild_toolbar()

        self.stats_lbl.height = dp(28 if wide else 24)
        self.stats_lbl.font_size = scaled_sp(12, profile_name)

        self.status_lbl.height = dp(28 if wide else 24)
        self.status_lbl.font_size = scaled_sp(11, profile_name)

        self.rv_layout.cols = cfg["cols"]
        self.rv_layout.default_size = (None, dp(cfg["row_h"]))
        self.rv_layout.spacing = dp(cfg["gap"])
        self.refresh_view()

    def _on_device_profile_selected(self, _spinner, text):
        self.apply_device_profile(text)
        self.save_preferences(device_profile=text)
        short_name = get_profile_config(text)["short"]
        self.status_line = f"Layout impostato su: {short_name}"

    def _do_logout(self):
        self.stop_flag = True
        self.player_window.close()
        if self.on_logout:
            self.on_logout()

    def _on_preset_selected(self, _spinner, text):
        url = PRESETS.get(text)
        if url:
            self.url_input.text = url
            self.load_from_url()

    def _on_player_mode_selected(self, _spinner, text):
        self.selected_player_mode = text
        self.save_preferences(player_mode=text)
        self.status_line = f"Modalità riproduzione: {text}"

    def set_filter(self, mode):
        self.filter_mode = mode
        for m, btn in self.filter_btns.items():
            btn.background_color = (0.15, 0.39, 0.92, 1) if m == mode else (0.18, 0.24, 0.36, 1)
        self.refresh_view()

    def _open_managed_popup(self, title, content, size_hint=None):
        if size_hint is None:
            size_hint = (0.76, 0.74) if is_wide_layout(self.device_profile) else (0.94, 0.78)
        if self.active_popup is not None:
            try:
                self.active_popup.dismiss()
            except Exception:
                pass
        popup = Popup(title=title, content=content, size_hint=size_hint)
        popup.bind(on_dismiss=lambda *_: setattr(self, "active_popup", None))
        self.active_popup = popup
        popup.open()
        return popup

    def open_lists_popup(self):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))

        if self.url_history:
            hist_spinner = Spinner(
                text="Cronologia URL recenti...",
                values=self.url_history,
                size_hint_y=None,
                height=dp(44),
                background_normal="",
                background_color=(0.14, 0.25, 0.42, 1),
                font_size=scaled_sp(12, self.device_profile),
            )

            def pick_hist(_s, val):
                if val and val != "Cronologia URL recenti...":
                    self.url_input.text = val
                    popup.dismiss()
                    self.load_from_url()

            hist_spinner.bind(text=pick_hist)
            box.add_widget(hist_spinner)

        ti = TextInput(
            text=Clipboard.paste() or "",
            hint_text="Incolla qui contenuto M3U o elenco URL...",
            font_size=scaled_sp(12, self.device_profile),
        )
        box.add_widget(ti)

        btns = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = self._open_managed_popup("Liste & Cronologia M3U", box)

        b_cancel = TVButton(text="Annulla", background_normal="", background_color=(0.25, 0.3, 0.4, 1))
        b_cancel.bind(on_release=popup.dismiss)

        b_ok = TVButton(text="Carica Lista", bold=True, background_normal="", background_color=(0.09, 0.64, 0.29, 1))

        def confirm(*_):
            raw = ti.text.strip()
            if raw:
                self.parse_m3u(raw)
            popup.dismiss()

        b_ok.bind(on_release=confirm)
        btns.add_widget(b_cancel)
        btns.add_widget(b_ok)
        box.add_widget(btns)
        Clock.schedule_once(lambda *_: setattr(b_ok, "focus", True), 0.1)

    def open_channel_details(self, uid):
        ch = next((c for c in self.channels if c["uid"] == uid), None)
        if not ch:
            return

        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        scroll = ScrollView(size_hint=(1, 1))

        details_lines = [
            f"Canale: {ch['name']}",
            f"Gruppo: {ch.get('group') or '-'}",
            f"TVG-ID: {ch.get('tvg_id') or '-'}",
            f"Stato: {ch['status']} ({ch['latency']}) — {ch['detail']}",
            f"Ora EPG: {ch.get('epg_now') or '-'}",
            f"Poi EPG: {ch.get('epg_next') or '-'}",
            f"User-Agent: {ch.get('ua') or 'Default'}",
            f"Referer: {ch.get('referer') or '-'}",
            f"URL: {ch['url']}",
        ]
        lbl = Label(
            text="\n".join(details_lines),
            halign="left",
            valign="top",
            size_hint_y=None,
            font_size=scaled_sp(12, self.device_profile),
            color=(0.9, 0.94, 0.99, 1),
        )
        lbl.bind(
            width=lambda w, val: setattr(w, "text_size", (val, None)),
            texture_size=lambda w, ts: setattr(w, "height", max(dp(140), ts[1] + dp(12))),
        )
        scroll.add_widget(lbl)
        box.add_widget(scroll)

        popup = self._open_managed_popup("Dettaglio Canale", box)

        row1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        b_copy = TVButton(
            text="Copia URL",
            background_normal="",
            background_color=(0.2, 0.27, 0.4, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        b_copy.bind(on_release=lambda *_: (Clipboard.copy(ch["url"]), setattr(self, "status_line", f"URL copiato: {ch['name']}")))

        b_test = TVButton(
            text="Testa Ora",
            background_normal="",
            background_color=(0.16, 0.45, 0.68, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        b_test.bind(on_release=lambda *_: (popup.dismiss(), self.check_single_channel(ch["uid"])))

        b_ext = TVButton(
            text="↗ Esterno",
            background_normal="",
            background_color=(0.85, 0.47, 0.02, 1),
            font_size=scaled_sp(12, self.device_profile),
        )
        b_ext.bind(
            on_release=lambda *_: (
                popup.dismiss(),
                setattr(self, "current_channel", ch),
                self.open_current_externally(mode="Player: Scelta App"),
            )
        )

        row1.add_widget(b_copy)
        row1.add_widget(b_test)
        row1.add_widget(b_ext)
        box.add_widget(row1)

        row2 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        b_play = TVButton(text="▶ Riproduci", bold=True, background_normal="", background_color=(0.09, 0.64, 0.29, 1))
        b_play.bind(on_release=lambda *_: (popup.dismiss(), self.play_channel(ch["uid"])))

        b_close = TVButton(text="Chiudi", background_normal="", background_color=(0.25, 0.3, 0.4, 1))
        b_close.bind(on_release=popup.dismiss)

        row2.add_widget(b_play)
        row2.add_widget(b_close)
        box.add_widget(row2)
        Clock.schedule_once(lambda *_: setattr(b_play, "focus", True), 0.1)

    def check_single_channel(self, uid):
        ch = next((c for c in self.channels if c["uid"] == uid), None)
        if not ch:
            return
        ch["status"] = "PENDING"
        ch["detail"] = "Verifica..."
        self.refresh_view()
        self.update_stats()
        self.status_line = f"Test singolo: {ch['name']}..."

        def run_one():
            self._check_one(ch, timeout=6)
            Clock.schedule_once(
                lambda *_: (
                    self.refresh_view(),
                    self.update_stats(),
                    setattr(self, "status_line", f"{ch['name']}: {ch['detail']} ({ch['latency']})"),
                )
            )

        threading.Thread(target=run_one, daemon=True).start()

    def open_epg_popup(self):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))

        spinner = Spinner(
            text="EPG Italia (iptv-epg)",
            values=list(EPG_PRESETS.keys()),
            size_hint_y=None,
            height=dp(44),
            background_normal="",
            background_color=(0.15, 0.22, 0.36, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        box.add_widget(spinner)

        epg_url_in = TextInput(
            text=EPG_PRESETS["EPG Italia (iptv-epg)"],
            multiline=False,
            size_hint_y=None,
            height=dp(46),
            hint_text="URL guida XMLTV (.xml o .xml.gz)...",
            background_color=(0.12, 0.16, 0.26, 1),
            foreground_color=(1, 1, 1, 1),
            font_size=scaled_sp(13, self.device_profile),
        )
        spinner.bind(text=lambda _s, txt: setattr(epg_url_in, "text", EPG_PRESETS.get(txt, epg_url_in.text)))
        box.add_widget(epg_url_in)

        info = Label(
            text="Scarica e associa programmi attuali e successivi tramite tvg-id o nome canale normalizzato.",
            font_size=scaled_sp(12, self.device_profile),
            color=(0.75, 0.82, 0.92, 1),
        )
        info.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(info)

        btns = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = self._open_managed_popup(
            "Carica Guida EPG (XMLTV)",
            box,
            size_hint=(0.76, 0.58) if is_wide_layout(self.device_profile) else (0.94, 0.56),
        )

        b_cancel = TVButton(text="Chiudi", background_normal="", background_color=(0.25, 0.3, 0.4, 1))
        b_cancel.bind(on_release=popup.dismiss)

        b_load = TVButton(text="Scarica EPG", bold=True, background_normal="", background_color=(0.16, 0.45, 0.68, 1))

        def do_load(*_):
            u = epg_url_in.text.strip()
            popup.dismiss()
            self.load_epg_from_url(u)

        b_load.bind(on_release=do_load)
        btns.add_widget(b_cancel)
        btns.add_widget(b_load)
        box.add_widget(btns)
        Clock.schedule_once(lambda *_: setattr(b_load, "focus", True), 0.1)

    def load_epg_from_url(self, url):
        if not url.lower().startswith(("http://", "https://")):
            self.status_line = "Inserisci un URL EPG http:// o https:// valido."
            return
        self.status_line = "Download guida EPG in corso..."

        def worker():
            tmp_path = None
            try:
                base_dir = self._data_dir()
                tmp_path = os.path.join(base_dir, "epg_temp.xml")

                req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
                with urllib.request.urlopen(req, timeout=30, context=self.ssl_ctx) as resp:
                    is_gzip = url.lower().endswith(".gz")
                    first_two = b""
                    with open(tmp_path, "wb") as out_f:
                        if is_gzip:
                            dec = gzip.GzipFile(fileobj=resp)
                            while True:
                                chunk = dec.read(65536)
                                if not chunk:
                                    break
                                out_f.write(chunk)
                        else:
                            while True:
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                if not first_two:
                                    first_two = chunk[:2]
                                out_f.write(chunk)

                if not is_gzip and first_two == b"\x1f\x8b":
                    gz_tmp = tmp_path + ".gz"
                    os.replace(tmp_path, gz_tmp)
                    with gzip.open(gz_tmp, "rb") as f_in, open(tmp_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    try:
                        os.remove(gz_tmp)
                    except Exception:
                        pass

                by_id, by_norm = self._parse_xmltv_file(tmp_path)
                Clock.schedule_once(lambda *_: self._on_epg_loaded(by_id, by_norm, None))
            except Exception as e:
                Clock.schedule_once(lambda *_: self._on_epg_loaded({}, {}, str(e)))
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    def _parse_xmltv_file(self, xml_path: str):
        id_to_norms = {}
        progs_by_id = {}
        now_dt = datetime.datetime.now()
        min_dt = now_dt - datetime.timedelta(hours=6)
        max_dt = now_dt + datetime.timedelta(hours=36)

        for _event, elem in ET.iterparse(xml_path, events=("end",)):
            tag = elem.tag
            if tag == "channel":
                cid = (elem.attrib.get("id") or "").strip()
                if cid:
                    norms = []
                    for dn in elem.findall("display-name"):
                        if dn.text and dn.text.strip():
                            nk = normalize_channel_key(dn.text)
                            if nk and nk not in norms:
                                norms.append(nk)
                    if norms:
                        id_to_norms[cid] = norms
                elem.clear()

            elif tag == "programme":
                ch_id = (elem.attrib.get("channel") or "").strip()
                start_dt = parse_xmltv_time(elem.attrib.get("start", ""))
                stop_dt = parse_xmltv_time(elem.attrib.get("stop", ""))
                title_el = elem.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""

                if ch_id and title and start_dt is not None:
                    end_ref = stop_dt or (start_dt + datetime.timedelta(hours=2))
                    if min_dt <= end_ref and start_dt <= max_dt:
                        progs_by_id.setdefault(ch_id, []).append((start_dt, stop_dt, title))
                elem.clear()

        for ch_id in progs_by_id:
            progs_by_id[ch_id].sort(key=lambda item: item[0])

        by_norm = {}
        for cid, progs in progs_by_id.items():
            for nk in id_to_norms.get(cid, []):
                by_norm.setdefault(nk, []).extend(progs)

        for nk in by_norm:
            by_norm[nk].sort(key=lambda item: item[0])

        return progs_by_id, by_norm

    def _lookup_epg_for_channel(self, ch, now_dt):
        prog_list = None
        tvg_id = ch.get("tvg_id", "")
        if tvg_id and tvg_id in self.epg_by_id:
            prog_list = self.epg_by_id[tvg_id]
        else:
            nk = normalize_channel_key(ch["name"])
            if nk and nk in self.epg_by_norm:
                prog_list = self.epg_by_norm[nk]

        if not prog_list:
            return "", ""

        current_title = ""
        next_title = ""
        for idx, (start_dt, stop_dt, title) in enumerate(prog_list):
            end_ref = stop_dt or (start_dt + datetime.timedelta(hours=1))
            if start_dt <= now_dt < end_ref:
                current_title = f"{start_dt.strftime('%H:%M')} {title}"
                if idx + 1 < len(prog_list):
                    n_start, _, n_title = prog_list[idx + 1]
                    next_title = f"{n_start.strftime('%H:%M')} {n_title}"
                break
            elif start_dt > now_dt and not next_title:
                next_title = f"{start_dt.strftime('%H:%M')} {title}"
                break

        return current_title, next_title

    def _apply_epg_to_channels(self):
        now_dt = datetime.datetime.now()
        matched = 0
        for ch in self.channels:
            now_p, next_p = self._lookup_epg_for_channel(ch, now_dt)
            ch["epg_now"] = now_p
            ch["epg_next"] = next_p
            if now_p or next_p:
                matched += 1
        return matched

    def _on_epg_loaded(self, by_id, by_norm, err):
        if err:
            self.status_line = f"Errore EPG: {err[:60]}"
            return
        self.epg_by_id = by_id
        self.epg_by_norm = by_norm
        matched = self._apply_epg_to_channels()
        self.refresh_view()
        self.status_line = f"EPG caricata ({len(by_id)} canali XMLTV, {matched} abbinati)."

    def load_from_url(self):
        url = self.url_input.text.strip()
        if not url.lower().startswith(("http://", "https://")):
            self.status_line = "Inserisci un URL http:// o https:// valido."
            return
        self.record_url_history(url)
        self.btn_load.disabled = True
        self.status_line = "Download playlist in corso..."

        def worker():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
                with urllib.request.urlopen(req, timeout=18, context=self.ssl_ctx) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                Clock.schedule_once(lambda *_: self._on_loaded(text, None))
            except Exception as e:
                Clock.schedule_once(lambda *_: self._on_loaded("", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self, text, err):
        self.btn_load.disabled = False
        if err:
            self.status_line = f"Errore download: {err[:60]}"
            return
        self.parse_m3u(text)

    def parse_m3u(self, content):
        saved_favs = self.load_saved_favorites()
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        parsed = []
        cur_extinf = None
        cur_name = None
        cur_group = ""
        cur_tvg_id = ""
        cur_ua = ""
        cur_ref = ""

        for line in lines:
            if line.startswith("#EXTM3U"):
                continue
            elif line.startswith("#EXTINF:"):
                cur_extinf = line
                m_grp = re.search(r'group-title\s*=\s*"([^"]*)"', line, re.IGNORECASE)
                if m_grp:
                    cur_group = m_grp.group(1).strip()
                m_id = re.search(r'tvg-id\s*=\s*"([^"]*)"', line, re.IGNORECASE)
                if m_id:
                    cur_tvg_id = m_id.group(1).strip()
                cur_name = line.split(",", 1)[1].strip() if "," in line else "Canale"
            elif line.startswith("#EXTVLCOPT:"):
                opt = line.split(":", 1)[1].strip()
                if opt.lower().startswith("http-user-agent="):
                    cur_ua = opt.split("=", 1)[1].strip()
                elif opt.lower().startswith("http-referrer="):
                    cur_ref = opt.split("=", 1)[1].strip()
            elif line.startswith("#"):
                continue
            else:
                clean_url = line
                ua = cur_ua
                ref = cur_ref
                if "|" in line:
                    base_part, pipe_part = line.split("|", 1)
                    clean_url = base_part.strip()
                    for kv in pipe_part.split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            kl = k.strip().lower()
                            val = urllib.parse.unquote(v.strip())
                            if kl == "user-agent" and not ua:
                                ua = val
                            elif kl in ("referer", "referrer") and not ref:
                                ref = val

                self.uid_seq += 1
                idx = len(parsed) + 1
                name = cur_name or f"Canale {idx}"
                parsed.append(
                    {
                        "uid": self.uid_seq,
                        "name": name,
                        "group": cur_group,
                        "tvg_id": cur_tvg_id,
                        "epg_now": "",
                        "epg_next": "",
                        "extinf": cur_extinf or f"#EXTINF:-1,{name}",
                        "url": clean_url,
                        "ua": ua,
                        "referer": ref,
                        "status": "PENDING",
                        "latency": "-",
                        "detail": "In attesa",
                        "favorite": clean_url in saved_favs,
                        "device_profile": self.device_profile,
                    }
                )
                cur_extinf = None
                cur_name = None
                cur_group = ""
                cur_tvg_id = ""
                cur_ua = ""
                cur_ref = ""

        self.channels = parsed
        if self.epg_by_id or self.epg_by_norm:
            self._apply_epg_to_channels()
        self.refresh_view()
        self.update_stats()
        self.status_line = f"Caricati {len(self.channels)} canali."

    def refresh_view(self):
        q = self.search_input.text.strip().lower()
        mode = self.filter_mode
        filtered = []
        for ch in self.channels:
            if mode == "ONLINE" and ch["status"] != "ONLINE":
                continue
            if mode == "OFFLINE" and ch["status"] != "OFFLINE":
                continue
            if mode == "FAV" and not ch.get("favorite", False):
                continue
            if q:
                haystack = f"{ch['name']} {ch.get('group', '')} {ch.get('epg_now', '')} {ch.get('epg_next', '')}".lower()
                if q not in haystack:
                    continue
            item = dict(ch)
            item["device_profile"] = self.device_profile
            filtered.append(item)
        self.rv_data = filtered

    def update_stats(self):
        tot = len(self.channels)
        on = sum(1 for c in self.channels if c["status"] == "ONLINE")
        off = sum(1 for c in self.channels if c["status"] == "OFFLINE")
        fav = sum(1 for c in self.channels if c.get("favorite"))
        self.stats_text = f"Totale: {tot}  |  Online: {on}  |  Offline: {off}  |  ★: {fav}"

    def toggle_favorite(self, uid):
        for ch in self.channels:
            if ch["uid"] == uid:
                ch["favorite"] = not ch.get("favorite", False)
                break
        self.save_favorites()
        self.refresh_view()
        self.update_stats()

    def _on_player_window_closed(self, err_msg=None):
        if err_msg:
            self.status_line = f"Errore ExoPlayer: {err_msg[:45]}"
        else:
            self.status_line = "ExoPlayer chiuso."

    def _step_channel(self, delta: int):
        if not self.channels:
            return
        if not self.current_channel:
            self.play_channel(self.channels[0]["uid"])
            return
        ids = [c["uid"] for c in self.channels]
        if self.current_channel["uid"] not in ids:
            self.play_channel(ids[0])
            return
        pos = ids.index(self.current_channel["uid"])
        new_pos = (pos + delta) % len(ids)
        self.play_channel(ids[new_pos])

    def play_prev_channel(self):
        self._step_channel(-1)

    def play_next_channel(self):
        self._step_channel(1)

    def open_current_externally(self, mode=None, resolved_url=None, resolved_ua=None):
        if not self.current_channel:
            return
        ch = self.current_channel
        chosen_mode = mode or self.selected_player_mode
        target_url = resolved_url or ch.get("resolved_url") or ch["url"]
        target_ua = resolved_ua or ch.get("resolved_ua") or ch.get("ua", "")

        self.player_window.close()
        self.status_line = f"Apertura ({chosen_mode}): {ch['name']}..."
        ok, msg = open_stream_with_mode(
            target_url,
            title=ch["name"],
            ua=target_ua,
            referer=ch.get("referer", ""),
            player_mode=chosen_mode,
        )
        if ok:
            self.status_line = f"{msg}: {ch['name']}"
        else:
            self.status_line = f"Nessun player video trovato ({msg[:40]})"

    def play_channel(self, uid):
        ch = next((c for c in self.channels if c["uid"] == uid), None)
        if not ch:
            return

        self.current_channel = ch
        if ch.get("resolved_url"):
            self._launch_resolved_channel(ch, ch["resolved_url"], ch.get("resolved_ua") or ch.get("ua", ""))
            return

        self.status_line = f"Risoluzione flusso: {ch['name']}..."

        def resolve_and_launch():
            final_url, eff_ua = resolve_stream_url(ch["url"], ch.get("ua", ""), self.ssl_ctx)
            ch["resolved_url"] = final_url
            ch["resolved_ua"] = eff_ua
            Clock.schedule_once(lambda *_: self._launch_resolved_channel(ch, final_url, eff_ua))

        threading.Thread(target=resolve_and_launch, daemon=True).start()

    def _launch_resolved_channel(self, ch, final_url, eff_ua):
        mode = self.selected_player_mode
        if mode != "Player: ExoPlayer" or platform != "android":
            self.open_current_externally(mode=mode, resolved_url=final_url, resolved_ua=eff_ua)
            return

        epg_txt = ch.get("epg_now") or ch.get("group") or final_url
        self.status_line = f"Avvio ExoPlayer: {ch['name']}..."
        self.player_window.open_or_update(
            final_url,
            ch["name"],
            ua=eff_ua,
            referer=ch.get("referer", ""),
            epg_info=epg_txt,
        )

    def _check_one(self, ch, timeout=5):
        if self.stop_flag:
            return ch
        url = ch["url"]
        if not url.lower().startswith(("http://", "https://")):
            ch["status"] = "OFFLINE"
            ch["latency"] = "-"
            ch["detail"] = "Non HTTP(S)"
            return ch

        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        forced_ua = (qs.get("forceUserAgent", [""])[0]).strip()
        headers = {
            "User-Agent": ch["ua"] or forced_ua or DEFAULT_UA,
            "Accept": "*/*",
            "Connection": "close",
        }
        if ch["referer"]:
            headers["Referer"] = ch["referer"]

        t0 = time.perf_counter()
        resp = None
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            resp = urllib.request.urlopen(req, timeout=timeout, context=self.ssl_ctx)
            code = resp.getcode()
            chunk = resp.read(2048)
            ms = int((time.perf_counter() - t0) * 1000)
            if code not in (200, 206) or not chunk:
                ch["status"] = "OFFLINE"
                ch["latency"] = f"{ms}ms"
                ch["detail"] = f"HTTP {code}"
                return ch

            sample = chunk.decode("utf-8", errors="ignore").lstrip()
            if sample.lower().startswith(("<!doctype html", "<html")):
                ch["status"] = "OFFLINE"
                ch["latency"] = f"{ms}ms"
                ch["detail"] = "Pagina HTML"
            elif sample.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in sample and "#EXTINF" not in sample:
                ch["status"] = "OFFLINE"
                ch["latency"] = f"{ms}ms"
                ch["detail"] = "HLS scaduto"
            else:
                ch["status"] = "ONLINE"
                ch["latency"] = f"{ms}ms"
                ch["detail"] = f"HTTP {code} OK"
        except urllib.error.HTTPError as e:
            ms = int((time.perf_counter() - t0) * 1000)
            ch["status"] = "OFFLINE"
            ch["latency"] = f"{ms}ms"
            ch["detail"] = f"HTTP {e.code}"
        except (socket.timeout, TimeoutError):
            ch["status"] = "OFFLINE"
            ch["latency"] = "-"
            ch["detail"] = "Timeout"
        except Exception as e:
            ch["status"] = "OFFLINE"
            ch["latency"] = "-"
            ch["detail"] = str(e)[:28]
        finally:
            if resp:
                try:
                    resp.close()
                except Exception:
                    pass
        return ch

    def start_check(self):
        if self.is_checking or not self.channels:
            return
        self.is_checking = True
        self.stop_flag = False
        self.btn_check.disabled = True
        self.btn_stop.disabled = False

        for ch in self.channels:
            ch["status"] = "PENDING"
            ch["latency"] = "-"
            ch["detail"] = "Verifica..."
        self.refresh_view()
        self.update_stats()

        targets = list(self.channels)
        total = len(targets)

        def pool_worker():
            done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                futs = [ex.submit(self._check_one, ch, 5) for ch in targets]
                for fut in concurrent.futures.as_completed(futs):
                    if self.stop_flag:
                        break
                    try:
                        fut.result()
                    except Exception:
                        pass
                    done += 1
                    if done % 5 == 0 or done == total:
                        d_snap = done
                        Clock.schedule_once(lambda *_: self._on_progress(d_snap, total))
            Clock.schedule_once(lambda *_: self._on_check_done())

        threading.Thread(target=pool_worker, daemon=True).start()

    def _on_progress(self, done, total):
        self.refresh_view()
        self.update_stats()
        self.status_line = f"Controllo flussi: {done}/{total}..."

    def _on_check_done(self):
        self.is_checking = False
        self.btn_check.disabled = False
        self.btn_stop.disabled = True
        self.refresh_view()
        self.update_stats()
        self.status_line = "Controllo completato."

    def stop_check(self):
        if self.is_checking:
            self.stop_flag = True
            self.status_line = "Interruzione in corso..."

    def export_online_m3u(self):
        valid = [c for c in self.channels if c["status"] == "ONLINE"]
        if not valid:
            valid = list(self.channels)
        if not valid:
            self.status_line = "Nessun canale da esportare."
            return

        valid.sort(key=lambda c: 0 if c.get("favorite") else 1)
        lines = ["#EXTM3U"]
        for ch in valid:
            lines.append(ch["extinf"])
            lines.append(ch["url"])
        m3u_text = "\n".join(lines) + "\n"

        out_file = os.path.join(self._data_dir(), "playlist_premium.m3u")
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(m3u_text)
        except Exception:
            pass

        ok, msg = share_text_externally(m3u_text, subject="IPTV Studio Premium - Playlist M3U", saved_path=out_file)
        self.status_line = msg if msg else f"Esportati {len(valid)} canali."


class IPTVStudioPremiumApp(App):
    def build(self):
        self.title = "IPTV Studio Premium"
        self.container = BoxLayout(orientation="vertical")
        self.root_widget = None
        self.current_profile = self._read_saved_profile()
        self._show_login()
        return self.container

    def _read_saved_profile(self):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            p = os.path.join(self.user_data_dir, "settings.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                prof = data.get("device_profile")
                if prof in DEVICE_PROFILES:
                    return prof
        except Exception:
            pass
        return DEFAULT_DEVICE_PROFILE

    def _persist_profile(self, profile_name: str):
        if profile_name not in DEVICE_PROFILES:
            return
        self.current_profile = profile_name
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            p = os.path.join(self.user_data_dir, "settings.json")
            data = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            data["device_profile"] = profile_name
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _show_login(self):
        self.container.clear_widgets()
        self.root_widget = None
        login_view = LoginScreen(
            on_authenticated=self._on_login_success,
            initial_profile=self.current_profile,
            on_profile_change=self._persist_profile,
        )
        self.container.add_widget(login_view)

    def _on_login_success(self, username="admin", playlist_url=None, epg_url=None, device_profile=None):
        if device_profile in DEVICE_PROFILES:
            self._persist_profile(device_profile)
        self.container.clear_widgets()
        self.root_widget = IPTVStudioPremiumRoot(
            username=username,
            initial_playlist_url=playlist_url,
            initial_epg_url=epg_url,
            initial_device_profile=self.current_profile,
            on_logout=self._show_login,
        )
        self.container.add_widget(self.root_widget)

    def on_stop(self):
        if self.root_widget is not None and getattr(self.root_widget, "player_window", None):
            self.root_widget.player_window.close()

    def toggle_favorite(self, uid):
        if self.root_widget:
            self.root_widget.toggle_favorite(uid)

    def open_channel_details(self, uid):
        if self.root_widget:
            self.root_widget.open_channel_details(uid)

    def play_channel(self, uid):
        if self.root_widget:
            self.root_widget.play_channel(uid)

    def set_status(self, txt):
        if self.root_widget:
            self.root_widget.status_line = txt



# --- Gestione telecomando e tastiera softinput su Android/TV Box ---
try:
    from kivy.core.window import Window as _RemoteWindow
    _RemoteWindow.softinput_mode = "below_target"

    def _remote_key_handler(_win, keycode, scancode, codepoint, modifiers):
        kname = keycode[1] if isinstance(keycode, (tuple, list)) and len(keycode) > 1 else str(keycode)
        print(f"[REMOTE] keycode={keycode} scancode={scancode} name={kname}")
        return False

    _RemoteWindow.bind(on_keyboard=_remote_key_handler)
except Exception as _remote_err:
    print(f"[REMOTE] Init warning: {_remote_err}")
# --- Fine gestione telecomando ---



# --- Gruppi Live / Film / Serie TV e Player Esterno VLC ---
def open_in_external_vlc(stream_url, title=""):
    """Apre un flusso su VLC per Android tramite Intent (con fallback sicuro)."""
    if not stream_url:
        print("[VLC] URL flusso vuoto.")
        return False
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")

        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(Uri.parse(stream_url), "video/*")
        intent.setPackage("org.videolan.vlc")
        intent.putExtra("title", title or "IPTV Stream")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        activity = PythonActivity.mActivity
        try:
            activity.startActivity(intent)
            return True
        except Exception:
            # Se VLC non e' installato o non risponde al package esplicito, apre chooser generico
            chooser = Intent.createChooser(Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse(stream_url), "video/*"), "Apri con player esterno")
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            activity.startActivity(chooser)
            return True
    except Exception as exc:
        print(f"[VLC] Fallback non-Android o errore Intent: {exc} | URL={stream_url}")
        return False


def attach_category_bar_to_root(app_instance, categories=("Live", "Film", "Serie TV")):
    """Aggiunge in cima alla schermata principale i pulsanti per Live, Film e Serie TV."""
    try:
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.metrics import dp

        root = getattr(app_instance, "root", None) or getattr(app_instance, "root_widget", None)
        if root is None or getattr(root, "_vlc_cat_bar_added", False):
            return

        bar = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(42), spacing=dp(6), padding=[dp(6), dp(4)])
        for cat in categories:
            btn = Button(
                text=cat,
                size_hint_x=1,
                focusable=False,
            )
            btn.bind(on_release=lambda _b, c=cat: _on_category_selected(app_instance, c))
            bar.add_widget(btn)

        if hasattr(root, "add_widget"):
            root.add_widget(bar, index=len(root.children))
            root._vlc_cat_bar_added = True
            print("[UI] Barra categorie Live / Film / Serie TV aggiunta.")
    except Exception as exc:
        print(f"[UI] Impossibile aggiungere barra categorie: {exc}")


def _on_category_selected(app_instance, category_name):
    print(f"[CAT] Selezionato gruppo: {category_name}")
    root = getattr(app_instance, "root", None) or getattr(app_instance, "root_widget", None)
    for attr in ("set_category", "filter_category", "select_group", "filter_group"):
        fn = getattr(app_instance, attr, None) or (getattr(root, attr, None) if root else None)
        if callable(fn):
            try:
                fn(category_name)
                return
            except Exception as exc:
                print(f"[CAT] Errore chiamando {attr}: {exc}")


try:
    from kivy.app import App as _KivyApp
    _orig_on_start = getattr(_KivyApp, "on_start", None)

    def _wrapped_on_start(self, *args, **kwargs):
        attach_category_bar_to_root(self)
        if callable(_orig_on_start):
            return _orig_on_start(self, *args, **kwargs)

    _KivyApp.on_start = _wrapped_on_start
except Exception as _app_hook_err:
    print(f"[UI] Hook on_start warning: {_app_hook_err}")
# --- Fine Gruppi Live / Film / Serie TV e Player Esterno VLC ---



# --- Estensione Categorie IPTV e Filtro Dinamico ---
DEFAULT_IPTV_CATEGORIES = [
    "Tutte",
    "Live",
    "Film",
    "Serie TV",
    "Sport",
    "Cinema",
    "News",
    "Bambini",
    "Documentari",
    "Musica",
]


def extract_categories_from_items(items, base_categories=None):
    """Estrae categorie uniche da dizionari/oggetti canale mantenendo l'ordine."""
    seen = []
    for c in (base_categories or DEFAULT_IPTV_CATEGORIES):
        if c not in seen:
            seen.append(c)

    if not items:
        return seen

    for item in items:
        cat = ""
        if isinstance(item, dict):
            cat = (
                item.get("category")
                or item.get("group")
                or item.get("group_title")
                or item.get("group-title")
                or ""
            )
        else:
            for attr in ("category", "group", "group_title"):
                val = getattr(item, attr, "")
                if val:
                    cat = val
                    break
        cat = str(cat).strip()
        if cat and cat not in seen:
            seen.append(cat)
    return seen


def filter_items_by_category(items, selected_category):
    """Filtra una lista di canali/contenuti in base alla categoria scelta."""
    if not items or not selected_category or selected_category == "Tutte":
        return list(items or [])

    target = selected_category.strip().lower()
    result = []
    for item in items:
        if isinstance(item, dict):
            raw_cat = (
                item.get("category")
                or item.get("group")
                or item.get("group_title")
                or item.get("group-title")
                or ""
            )
            name = str(item.get("name") or item.get("title") or "")
        else:
            raw_cat = (
                getattr(item, "category", "")
                or getattr(item, "group", "")
                or getattr(item, "group_title", "")
            )
            name = str(getattr(item, "name", "") or getattr(item, "title", ""))

        cat_l = str(raw_cat).strip().lower()
        name_l = name.strip().lower()
        if target in cat_l or cat_l == target or (target in ("film", "serie tv", "sport", "news") and target in name_l):
            result.append(item)
    return result


def build_category_scroll_bar(on_select_cb, categories=None, height_dp=42):
    """Crea una barra orizzontale scorrevole di pulsanti categoria compatibile con D-Pad/touch."""
    from kivy.metrics import dp
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.scrollview import ScrollView

    cats = list(categories or DEFAULT_IPTV_CATEGORIES)
    sv = ScrollView(
        size_hint_y=None,
        height=dp(height_dp),
        do_scroll_x=True,
        do_scroll_y=False,
        bar_width=dp(3),
    )
    row = BoxLayout(
        orientation="horizontal",
        size_hint_x=None,
        size_hint_y=1,
        spacing=dp(6),
        padding=[dp(6), dp(3)],
    )
    row.bind(minimum_width=row.setter("width"))

    for cat in cats:
        btn = Button(
            text=cat,
            size_hint_x=None,
            width=dp(max(86, len(cat) * 10 + 28)),
            focusable=False,
        )
        btn.bind(on_release=lambda _b, c=cat: on_select_cb(c))
        row.add_widget(btn)

    sv.add_widget(row)
    return sv
# --- Fine Estensione Categorie IPTV ---



# --- Navigazione D-Pad, Evidenziazione e Scorrimento Automatico ---
class _DPadNavigator:
    def __init__(self):
        self.items = []
        self.index = -1
        self._highlight_canvas = None
        self._highlight_color = None
        self._highlight_line = None

    def register_widgets(self, widgets):
        self.items = [w for w in (widgets or []) if w is not None]
        if self.items and (self.index < 0 or self.index >= len(self.items)):
            self.select(0)
        elif not self.items:
            self.index = -1
            self._clear_highlight()

    def _clear_highlight(self):
        try:
            if self._highlight_color is not None:
                self._highlight_color.a = 0.0
        except Exception:
            pass

    def _ensure_highlight_on(self, widget):
        from kivy.graphics import Color as KColor, Line as KLine
        from kivy.metrics import dp

        self._clear_highlight()
        if widget is None or not getattr(widget, "canvas", None):
            return
        with widget.canvas.after:
            self._highlight_color = KColor(0.98, 0.80, 0.08, 0.95)
            self._highlight_line = KLine(
                rectangle=(widget.x + 1, widget.y + 1, max(0, widget.width - 2), max(0, widget.height - 2)),
                width=dp(2.5),
            )

        def _sync(*_):
            try:
                if self._highlight_line is not None:
                    self._highlight_line.rectangle = (
                        widget.x + 1,
                        widget.y + 1,
                        max(0, widget.width - 2),
                        max(0, widget.height - 2),
                    )
            except Exception:
                pass

        widget.unbind(pos=_sync, size=_sync)
        widget.bind(pos=_sync, size=_sync)

    def _scroll_into_view(self, widget):
        from kivy.uix.scrollview import ScrollView
        parent = getattr(widget, "parent", None)
        while parent is not None:
            if isinstance(parent, ScrollView):
                try:
                    parent.scroll_to(widget, padding=dp(12), animate=True)
                except Exception:
                    pass
                break
            parent = getattr(parent, "parent", None)

    def select(self, idx):
        if not self.items:
            return
        self.index = max(0, min(idx, len(self.items) - 1))
        target = self.items[self.index]
        self._ensure_highlight_on(target)
        self._scroll_into_view(target)
        print(f"[DPAD] Selezionato indice {self.index}: {getattr(target, 'text', target)}")

    def move(self, delta):
        if not self.items:
            # Raccoglie automaticamente pulsanti/elementi visibili se non registrati
            self.items = _collect_focusable_widgets()
        if not self.items:
            return
        if self.index < 0:
            self.select(0)
        else:
            self.select((self.index + delta) % len(self.items))

    def activate(self):
        if not self.items:
            self.items = _collect_focusable_widgets()
        if 0 <= self.index < len(self.items):
            target = self.items[self.index]
            print(f"[DPAD] Attivazione su: {getattr(target, 'text', target)}")
            if hasattr(target, "trigger_action"):
                target.trigger_action(duration=0.05)
            elif hasattr(target, "dispatch"):
                target.dispatch("on_release")


def _collect_focusable_widgets():
    from kivy.app import App
    from kivy.uix.button import Button

    app = App.get_running_app()
    root = getattr(app, "root", None) if app else None
    found = []

    def _walk(w):
        if w is None or getattr(w, "disabled", False):
            return
        if getattr(w, "opacity", 1) == 0:
            return
        if isinstance(w, Button):
            found.append(w)
        for child in reversed(getattr(w, "children", [])):
            _walk(child)

    _walk(root)
    return found


DPAD_NAV = _DPadNavigator()

try:
    from kivy.core.window import Window as _DPadWindow

    def _dpad_key_handler(_win, keycode, scancode, codepoint, modifiers):
        kname = keycode[1] if isinstance(keycode, (tuple, list)) and len(keycode) > 1 else str(keycode)
        if kname in ("down", "right"):
            DPAD_NAV.move(1)
            return True
        if kname in ("up", "left"):
            DPAD_NAV.move(-1)
            return True
        if kname in ("enter", "numpadenter", "spacebar", "select"):
            DPAD_NAV.activate()
            return True
        return False

    _DPadWindow.bind(on_keyboard=_dpad_key_handler)
except Exception as _dpad_err:
    print(f"[DPAD] Init warning: {_dpad_err}")
# --- Fine Navigazione D-Pad, Evidenziazione e Scorrimento Automatico ---



# --- Fix Mouse e Scorrimento Barra Laterale (ScrollView) ---
def _enable_mouse_and_sidebar_scroll(root_widget):
    """Assicura che ScrollView e pulsanti rispondano a mouse, touch e rotellina."""
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.button import Button
    from kivy.metrics import dp

    def _walk(w):
        if w is None:
            return
        if isinstance(w, ScrollView):
            w.do_scroll_x = bool(getattr(w, "do_scroll_x", False))
            w.do_scroll_y = True
            w.scroll_wheel_distance = dp(48)
            # Se ha un solo figlio contenitore (es. BoxLayout/GridLayout), collega l'altezza minima
            if len(w.children) == 1:
                child = w.children[0]
                if getattr(child, "size_hint_y", None) is None and hasattr(child, "minimum_height"):
                    child.bind(minimum_height=child.setter("height"))
        if isinstance(w, Button):
            # Permette sia clic mouse/touch sia attivazione pulita
            if getattr(w, "disabled", False):
                w.disabled = False
        for ch in list(getattr(w, "children", [])):
            _walk(ch)

    _walk(root_widget)


try:
    from kivy.core.window import Window as _MouseWin
    from kivy.app import App as _MouseApp

    def _on_win_mouse_scroll(_win, x, y, button, modifiers):
        app = _MouseApp.get_running_app()
        root = getattr(app, "root", None) if app else None
        if root is None:
            return False
        from kivy.uix.scrollview import ScrollView
        step = 0.12
        scrolled = False

        def _scroll_under(w):
            nonlocal scrolled
            if w is None or getattr(w, "opacity", 1) == 0:
                return
            if isinstance(w, ScrollView) and w.collide_point(x, y):
                if button == "scrollup":
                    w.scroll_y = min(1.0, w.scroll_y + step)
                    scrolled = True
                elif button == "scrolldown":
                    w.scroll_y = max(0.0, w.scroll_y - step)
                    scrolled = True
            for ch in getattr(w, "children", []):
                _scroll_under(ch)

        _scroll_under(root)
        return scrolled

    _MouseWin.bind(on_mouse_down=_on_win_mouse_scroll)
except Exception as _mw_err:
    print(f"[MOUSE] Init warning: {_mw_err}")


try:
    from kivy.app import App as _FixApp
    _prev_on_start_ms = getattr(_FixApp, "on_start", None)

    def _wrapped_on_start_ms(self, *args, **kwargs):
        res = _prev_on_start_ms(self, *args, **kwargs) if callable(_prev_on_start_ms) else None
        root = getattr(self, "root", None)
        if root is not None:
            _enable_mouse_and_sidebar_scroll(root)
        return res

    _FixApp.on_start = _wrapped_on_start_ms
except Exception as _ms_err:
    print(f"[MOUSE] on_start hook warning: {_ms_err}")
# --- Fine Fix Mouse e Scorrimento Barra Laterale ---



# --- Fix Mirato Scorrimento Barra Laterale v2 ---
def _patch_sidebar_scrollviews(root_widget):
    from kivy.uix.scrollview import ScrollView
    from kivy.metrics import dp

    def _walk(w):
        if w is None:
            return
        if isinstance(w, ScrollView):
            w.do_scroll_y = True
            w.effect_cls = w.effect_cls or "ScrollEffect"
            w.scroll_wheel_distance = dp(56)
            for child in list(w.children):
                if hasattr(child, "bind") and hasattr(child, "minimum_height"):
                    child.size_hint_y = None
                    child.bind(minimum_height=child.setter("height"))
        for ch in list(getattr(w, "children", [])):
            _walk(ch)

    _walk(root_widget)


try:
    from kivy.core.window import Window as _WinV2
    from kivy.app import App as _AppV2
    from kivy.uix.scrollview import ScrollView as _SVV2

    def _find_target_scrollview(root, x, y):
        svs = []
        def _collect(w):
            if w is None or getattr(w, "opacity", 1) == 0:
                return
            if isinstance(w, _SVV2):
                svs.append(w)
            for ch in getattr(w, "children", []):
                _collect(ch)
        _collect(root)

        # Prima cerca una ScrollView sotto il cursore (x, y)
        for sv in svs:
            try:
                if sv.collide_point(x, y):
                    return sv
            except Exception:
                pass
        # Fallback: se clicchi sulla fascia sinistra (barra laterale), usa la ScrollView più a sinistra
        if svs and x <= _WinV2.width * 0.35:
            return min(svs, key=lambda s: s.x)
        return None

    def _on_win_touch_down_v2(_win, touch):
        app = _AppV2.get_running_app()
        root = getattr(app, "root", None) if app else None
        if root is None:
            return False
        btn = getattr(touch, "button", None)
        if btn in ("scrollup", "scrolldown"):
            sv = _find_target_scrollview(root, touch.x, touch.y)
            if sv is not None:
                step = 0.14
                if btn == "scrollup":
                    sv.scroll_y = min(1.0, sv.scroll_y + step)
                else:
                    sv.scroll_y = max(0.0, sv.scroll_y - step)
                return True
        return False

    _WinV2.bind(on_touch_down=_on_win_touch_down_v2)

    _orig_start_v2 = getattr(_AppV2, "on_start", None)
    def _wrapped_start_v2(self, *args, **kwargs):
        res = _orig_start_v2(self, *args, **kwargs) if callable(_orig_start_v2) else None
        if getattr(self, "root", None) is not None:
            _patch_sidebar_scrollviews(self.root)
        return res
    _AppV2.on_start = _wrapped_start_v2
except Exception as _err_v2:
    print(f"[SIDEBAR_V2] Init warning: {_err_v2}")
# --- Fine Fix Mirato Scorrimento Barra Laterale v2 ---



# --- Fix Telecomando D-Pad e Blocco Tastiera Virtuale ---
def _disable_softkeyboard_on_non_text(root_widget):
    from kivy.uix.textinput import TextInput

    def _walk(w):
        if w is None:
            return
        if not isinstance(w, TextInput):
            if hasattr(w, "keyboard_mode"):
                try:
                    w.keyboard_mode = "none"
                except Exception:
                    pass
            if getattr(w, "focus", False):
                try:
                    w.focus = False
                except Exception:
                    pass
        for ch in list(getattr(w, "children", [])):
            _walk(ch)

    _walk(root_widget)


try:
    from kivy.core.window import Window as _RemKbdWin
    from kivy.app import App as _RemKbdApp
    from kivy.uix.textinput import TextInput as _KTextInput

    try:
        _RemKbdWin.softinput_mode = ""
    except Exception:
        pass

    def _force_hide_keyboard(*_):
        try:
            _RemKbdWin.hide_keyboard()
        except Exception:
            pass

    def _on_rem_key_down(_win, keycode, scancode, codepoint, modifiers):
        kname = keycode[1] if isinstance(keycode, (tuple, list)) and len(keycode) > 1 else str(keycode)
        focused = getattr(_RemKbdWin, "focus_next", None)
        # Se non siamo dentro un TextInput attivo, blocca apertura tastiera su Invio/OK/frecce
        is_text = isinstance(getattr(_RemKbdApp.get_running_app(), "root", None), _KTextInput)
        if not is_text and kname in ("enter", "numpadenter", "spacebar", "select", "up", "down", "left", "right"):
            _force_hide_keyboard()
            if "DPAD_NAV" in globals():
                if kname in ("down", "right"):
                    globals()["DPAD_NAV"].move(1)
                    return True
                if kname in ("up", "left"):
                    globals()["DPAD_NAV"].move(-1)
                    return True
                if kname in ("enter", "numpadenter", "spacebar", "select"):
                    globals()["DPAD_NAV"].activate()
                    return True
            return True
        return False

    _RemKbdWin.bind(on_key_down=_on_rem_key_down)

    _orig_start_rk = getattr(_RemKbdApp, "on_start", None)

    def _wrapped_start_rk(self, *args, **kwargs):
        res = _orig_start_rk(self, *args, **kwargs) if callable(_orig_start_rk) else None
        if getattr(self, "root", None) is not None:
            _disable_softkeyboard_on_non_text(self.root)
        _force_hide_keyboard()
        return res

    _RemKbdApp.on_start = _wrapped_start_rk
except Exception as _rk_err:
    print(f"[REM_KBD] Init warning: {_rk_err}")
# --- Fine Fix Telecomando D-Pad e Blocco Tastiera Virtuale ---


if __name__ == "__main__":
    IPTVStudioPremiumApp().run()
