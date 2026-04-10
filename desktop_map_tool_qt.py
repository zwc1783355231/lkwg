import argparse
import ctypes
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab
from PyQt5 import QtCore, QtGui, QtWidgets

from desktop_map_tool import (
    CONFIG_PATH as LEGACY_CONFIG_PATH,
    DATA_DIR,
    DEFAULT_ICON_SIZE,
    DEFAULT_MAP_OPACITY,
    DEFAULT_MATCH_MAP_SIZE_1X,
    DEFAULT_MINIMAP_LEFT,
    DEFAULT_MINIMAP_SIZE,
    DEFAULT_MINIMAP_TOP,
    DEFAULT_OVERLAY_POSITION,
    DEFAULT_TRACK_INTERVAL_MS,
    DEFAULT_TRACK_MATCH_THRESHOLD,
    DEFAULT_TRACK_SEARCH_WINDOW,
    DEFAULT_VIEW_SCALE,
    IMAGE_DIR,
    MAP_IMAGE_PATH,
    MATCH_METHOD_OPTIONS,
    MATCH_SCALE_OPTIONS,
    MAJOR_COLORS,
    MAX_SCALE,
    MIN_SCALE,
    MiniMapMatcher,
    OUTLINE_COLORS,
    OVERLAY_POSITION_OPTIONS,
    TEMP_DIR,
    USER_DATA_DIR,
    USER_SELECTION_PATH,
    load_major_files,
)


BASE_DIR = Path(__file__).resolve().parent
QT_CONFIG_PATH = BASE_DIR / "qt_app_config.json"
MAP_WINDOW_GAP = 8
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

PATH_COLORS = {
    "红色": "#ff4b4b",
    "黄色": "#ffd84d",
    "青色": "#3bd1ff",
    "绿色": "#32c36a",
    "白色": "#ffffff",
}

MAP_WINDOW_POSITION_OPTIONS = ("右侧", "左侧", "上方", "下方")


def validate_inputs():
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"缺少 data 目录: {DATA_DIR}")
    if not MAP_IMAGE_PATH.exists():
        raise FileNotFoundError(f"缺少地图图片: {MAP_IMAGE_PATH}")
    if not IMAGE_DIR.exists():
        raise FileNotFoundError(f"缺少图标目录: {IMAGE_DIR}")
    json_files = [path for path in DATA_DIR.glob("*.json") if path.name != "_meta.json"]
    if not json_files:
        raise FileNotFoundError(f"data 目录中没有大类 json: {DATA_DIR}")


def load_dataset():
    majors = load_major_files()
    subcategory_defs = []
    points = []
    for major in majors:
        major_id = major["major_id"]
        major_name = major["major_name"]
        color = MAJOR_COLORS.get(major_name, "#e9724c")
        for sub in major["subcategories"]:
            sub_def = {
                "major_id": major_id,
                "major_name": major_name,
                "sub_id": sub["sub_id"],
                "sub_name": sub["sub_name"],
                "source": sub["source"],
                "count": len(sub["items"]),
                "filename": major["_filename"],
                "image_file": sub.get("image_file", ""),
            }
            subcategory_defs.append(sub_def)
            for item in sub["items"]:
                points.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "x_map": int(item["x_map"]),
                        "y_map": int(item["y_map"]),
                        "source": item["source"],
                        "raw": item["raw"],
                        "major_id": major_id,
                        "major_name": major_name,
                        "sub_id": sub["sub_id"],
                        "sub_name": sub["sub_name"],
                        "color": color,
                        "image_file": sub.get("image_file", ""),
                    }
                )
    subcategory_defs.sort(key=lambda row: (row["major_id"], row["sub_id"]))
    points.sort(key=lambda row: (row["major_id"], row["sub_id"], row["id"]))
    return majors, subcategory_defs, points


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def seed_config():
    legacy = load_json_file(LEGACY_CONFIG_PATH, {})
    normal_geometry = legacy.get("normal_window_geometry", {})
    return {
        "window_width": 560,
        "window_height": int(normal_geometry.get("height", 940)),
        "window_x": normal_geometry.get("x"),
        "window_y": normal_geometry.get("y"),
        "sidebar_width": 450,
        "map_window_width": int(normal_geometry.get("width", 1200)),
        "map_window_height": int(normal_geometry.get("height", 940)),
        "map_window_position": "右侧",
        "map_window_x": normal_geometry.get("x"),
        "map_window_y": normal_geometry.get("y"),
        "icon_size": int(legacy.get("icon_size", DEFAULT_ICON_SIZE)),
        "map_opacity": int(legacy.get("map_opacity", int(DEFAULT_MAP_OPACITY * 100))),
        "outline_color": str(legacy.get("outline_color", "白色")),
        "path_color": "红色",
        "view_scale": float(legacy.get("view_scale", DEFAULT_VIEW_SCALE) or DEFAULT_VIEW_SCALE),
        "view_center_x": legacy.get("view_center_x"),
        "view_center_y": legacy.get("view_center_y"),
        "match_map_size": int(legacy.get("match_map_size", DEFAULT_MATCH_MAP_SIZE_1X)),
        "match_scale": str(legacy.get("match_scale", "1x")),
        "match_method": str(legacy.get("match_method", "RGB")),
        "overlay_position": str(legacy.get("overlay_position", DEFAULT_OVERLAY_POSITION)),
        "topmost": False,
        "track_follow": bool(legacy.get("track_follow", True)),
        "track_interval_ms": int(legacy.get("track_interval_ms", DEFAULT_TRACK_INTERVAL_MS)),
        "track_search_window": int(legacy.get("track_search_window", DEFAULT_TRACK_SEARCH_WINDOW)),
        "track_match_threshold": float(legacy.get("track_match_threshold", DEFAULT_TRACK_MATCH_THRESHOLD)),
        "minimap_left": int(legacy.get("minimap_left", DEFAULT_MINIMAP_LEFT)),
        "minimap_top": int(legacy.get("minimap_top", DEFAULT_MINIMAP_TOP)),
        "minimap_size": int(legacy.get("minimap_size", DEFAULT_MINIMAP_SIZE)),
        "debug_mode": bool(legacy.get("debug_mode", False)),
        "error_curve": bool(legacy.get("error_curve", False)),
        "log_visible": bool(legacy.get("log_expanded", False)),
        "tools_expanded": False,
        "markers_expanded": False,
        "detail_expanded": False,
        "status_expanded": False,
    }


def load_qt_config():
    cfg = seed_config()
    loaded = load_json_file(QT_CONFIG_PATH, {})
    if isinstance(loaded, dict):
        cfg.update(loaded)
    cfg["window_width"] = max(420, min(900, int(cfg.get("window_width", 560))))
    cfg["window_height"] = max(420, int(cfg.get("window_height", 940)))
    cfg["map_window_width"] = max(360, int(cfg.get("map_window_width", 1200)))
    cfg["map_window_height"] = max(260, int(cfg.get("map_window_height", 940)))
    if cfg.get("map_window_position") not in MAP_WINDOW_POSITION_OPTIONS:
        cfg["map_window_position"] = "右侧"
    cfg["icon_size"] = max(12, min(48, int(cfg.get("icon_size", DEFAULT_ICON_SIZE))))
    cfg["map_opacity"] = max(15, min(100, int(cfg.get("map_opacity", int(DEFAULT_MAP_OPACITY * 100)))))
    cfg["view_scale"] = max(MIN_SCALE, min(MAX_SCALE, float(cfg.get("view_scale", DEFAULT_VIEW_SCALE))))
    cfg["match_map_size"] = max(512, min(20000, int(cfg.get("match_map_size", DEFAULT_MATCH_MAP_SIZE_1X))))
    cfg["match_scale"] = cfg.get("match_scale") if cfg.get("match_scale") in MATCH_SCALE_OPTIONS else "1x"
    cfg["match_method"] = cfg.get("match_method") if cfg.get("match_method") in MATCH_METHOD_OPTIONS else "RGB"
    cfg["overlay_position"] = cfg.get("overlay_position") if cfg.get("overlay_position") in OVERLAY_POSITION_OPTIONS else DEFAULT_OVERLAY_POSITION
    cfg["track_interval_ms"] = max(10, min(5000, int(cfg.get("track_interval_ms", DEFAULT_TRACK_INTERVAL_MS))))
    cfg["track_search_window"] = max(200, min(4000, int(cfg.get("track_search_window", DEFAULT_TRACK_SEARCH_WINDOW))))
    cfg["track_match_threshold"] = max(0.0, min(1.0, float(cfg.get("track_match_threshold", DEFAULT_TRACK_MATCH_THRESHOLD))))
    cfg["minimap_size"] = max(80, min(360, int(cfg.get("minimap_size", DEFAULT_MINIMAP_SIZE))))
    if cfg.get("outline_color") not in OUTLINE_COLORS:
        cfg["outline_color"] = "白色"
    if cfg.get("path_color") not in PATH_COLORS:
        cfg["path_color"] = "红色"
    return cfg


def save_qt_config(cfg: dict):
    QT_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_user_selection():
    payload = load_json_file(USER_SELECTION_PATH, {})
    if isinstance(payload, dict):
        items = payload.get("selected_sub_ids", [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    return {str(item) for item in items if item}


def save_user_selection(selected_sub_ids):
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"selected_sub_ids": sorted(selected_sub_ids)}
    USER_SELECTION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pil_to_pixmap(image: Image.Image) -> QtGui.QPixmap:
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    qimage = QtGui.QImage(data, image.width, image.height, image.width * 4, QtGui.QImage.Format_RGBA8888)
    return QtGui.QPixmap.fromImage(qimage.copy())


def render_icon_with_outline(base_image: Image.Image, size: int, outline_color: str) -> Image.Image:
    size = max(12, min(48, int(size)))
    badge_size = size + 8
    canvas = Image.new("RGBA", (badge_size, badge_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((1, 1, badge_size - 2, badge_size - 2), fill=(0, 0, 0, 92), outline=outline_color, width=2)
    icon = base_image.copy()
    icon.thumbnail((size, size), Image.LANCZOS)
    x = (badge_size - icon.width) // 2
    y = (badge_size - icon.height) // 2
    canvas.paste(icon, (x, y), icon)
    return canvas


def set_window_clickthrough(widget: QtWidgets.QWidget, enabled: bool):
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


class SignalBus(QtCore.QObject):
    log_message = QtCore.pyqtSignal(str)
    locate_success = QtCore.pyqtSignal(object, bool, bool, object, object)
    locate_failed = QtCore.pyqtSignal(str, bool)
    tracking_failed = QtCore.pyqtSignal(str)
    tracking_stopped = QtCore.pyqtSignal(str)


class MarkerItem(QtWidgets.QGraphicsObject):
    def __init__(self, point: dict, pixmap: QtGui.QPixmap | None, select_callback):
        super().__init__()
        self.point = point
        self.pixmap = pixmap
        self.select_callback = select_callback
        self.radius = 6.0
        self.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setPos(float(point["x_map"]), float(point["y_map"]))

    def boundingRect(self):
        if self.pixmap is not None and not self.pixmap.isNull():
            return QtCore.QRectF(
                -self.pixmap.width() / 2.0,
                -self.pixmap.height() / 2.0,
                float(self.pixmap.width()),
                float(self.pixmap.height()),
            )
        d = self.radius * 2 + 4
        return QtCore.QRectF(-d / 2.0, -d / 2.0, d, d)

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.pixmap is not None and not self.pixmap.isNull():
            painter.drawPixmap(int(-self.pixmap.width() / 2), int(-self.pixmap.height() / 2), self.pixmap)
            return
        painter.setBrush(QtGui.QColor(self.point["color"]))
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.2))
        painter.drawEllipse(QtCore.QPointF(0.0, 0.0), self.radius, self.radius)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.select_callback is not None:
            self.select_callback(self.point)
        super().mousePressEvent(event)


class ErrorCurveWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = []
        self.setMinimumSize(360, 220)

    def set_history(self, history):
        self.history = list(history)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#fffdf8"))
        left_pad, right_pad, top_pad, bottom_pad = 46, 18, 18, 34
        plot = QtCore.QRectF(left_pad, top_pad, max(10, rect.width() - left_pad - right_pad), max(10, rect.height() - top_pad - bottom_pad))
        painter.setPen(QtGui.QPen(QtGui.QColor("#d6cfbf"), 1))
        painter.drawRect(plot)
        if not self.history:
            painter.setPen(QtGui.QColor("#6c6c6c"))
            painter.drawText(rect, QtCore.Qt.AlignCenter, "暂无误差数据")
            return
        valid_scores = [score for _, row in self.history for score in row if score is not None]
        if not valid_scores:
            painter.setPen(QtGui.QColor("#6c6c6c"))
            painter.drawText(rect, QtCore.Qt.AlignCenter, "暂无误差数据")
            return
        min_score = min(valid_scores)
        max_score = max(valid_scores)
        span = max_score - min_score
        pad = max(span * 0.12, 1e-4) if span >= 1e-6 else max(1e-4, abs(max_score) * 0.1 + 1e-4)
        plot_min = max(0.0, min_score - pad)
        plot_max = max_score + pad
        plot_span = max(1e-9, plot_max - plot_min)
        colors = [QtGui.QColor("#ff4b4b"), QtGui.QColor("#2f8cff"), QtGui.QColor("#30a46c")]
        labels = ["Top1", "Top2", "Top3"]
        count = max(1, len(self.history) - 1)
        for idx, (label, color) in enumerate(zip(labels, colors)):
            path = QtGui.QPainterPath()
            started = False
            for pos, (_frame_idx, row) in enumerate(self.history):
                score = row[idx]
                if score is None:
                    continue
                x = plot.left() + (pos / count) * plot.width()
                y = plot.top() + (1.0 - (score - plot_min) / plot_span) * plot.height()
                if not started:
                    path.moveTo(x, y)
                    started = True
                else:
                    path.lineTo(x, y)
            painter.setPen(QtGui.QPen(color, 2))
            painter.drawPath(path)
            painter.drawText(rect.width() - 72, 18 + idx * 18, label)
        painter.setPen(QtGui.QColor("#6c6c6c"))
        painter.drawText(0, 0, int(left_pad - 8), int(top_pad + 10), QtCore.Qt.AlignRight, f"{plot_max:.3f}")
        painter.drawText(0, int(plot.bottom()) - 6, int(left_pad - 8), 16, QtCore.Qt.AlignRight, f"{plot_min:.3f}")
        painter.drawText(rect, QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter, "最近匹配帧")


class MinimapSelectorWindow(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(int, int, int)

    def __init__(self, size: int, x: int, y: int):
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.size_value = int(size)
        self.drag_offset = QtCore.QPoint()
        self.set_selector_geometry(int(x), int(y), int(size), emit_signal=False)

    def set_selector_geometry(self, x: int, y: int, size: int, emit_signal: bool = True):
        self.size_value = max(80, min(360, int(size)))
        self.setGeometry(int(x), int(y), self.size_value, self.size_value)
        self.update()
        if emit_signal:
            self.changed.emit(self.x(), self.y(), self.size_value)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.drag_offset = event.pos()

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self.move(event.globalPos() - self.drag_offset)
            self.changed.emit(self.x(), self.y(), self.size_value)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor("#00e6ff"), 3))
        painter.setBrush(QtCore.Qt.NoBrush)
        pad = 3
        painter.drawEllipse(pad, pad, self.width() - pad * 2, self.height() - pad * 2)


class CollapsibleSection(QtWidgets.QWidget):
    toggled = QtCore.pyqtSignal(bool)

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._expanded = bool(expanded)
        self.header = QtWidgets.QWidget()
        self.header.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.header_layout = QtWidgets.QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(6)
        self.arrow_label = QtWidgets.QLabel()
        self.arrow_label.setFixedWidth(12)
        self.arrow_label.setStyleSheet("color: #586779; font-size: 12px;")
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("color: #22313f; font-size: 14px; font-weight: 600;")
        self.header_layout.addWidget(self.arrow_label)
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch(1)
        self.header.mousePressEvent = self._header_mouse_press
        layout.addWidget(self.header)

        self.body = QtWidgets.QWidget()
        self.body.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.body.setVisible(self._expanded)
        layout.addWidget(self.body)
        self._apply_state()

    def setContent(self, widget: QtWidgets.QWidget):
        self.body_layout.addWidget(widget)

    def isExpanded(self) -> bool:
        return self._expanded

    def setExpanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._apply_state()

    def _header_mouse_press(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._expanded = not self._expanded
            self._apply_state()
            self.toggled.emit(self._expanded)
        event.accept()

    def _apply_state(self):
        self.arrow_label.setText("▾" if self._expanded else "▸")
        self.body.setVisible(self._expanded)
        self.body.setMaximumHeight(16777215 if self._expanded else 0)
        if self._expanded:
            self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
            self.body.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        else:
            self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            self.body.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.body.updateGeometry()
        self.updateGeometry()


class MapGraphicsView(QtWidgets.QGraphicsView):
    viewChanged = QtCore.pyqtSignal(dict)
    markerSelected = QtCore.pyqtSignal(dict)

    def __init__(self, map_path: Path, parent=None, transparent_background: bool = False, interactive_navigation: bool = True):
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.SmoothPixmapTransform
            | QtGui.QPainter.TextAntialiasing
        )
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.interactive_navigation = bool(interactive_navigation)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag if self.interactive_navigation else QtWidgets.QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        if transparent_background:
            self.setStyleSheet("background: transparent; border: none;")
            self.viewport().setStyleSheet("background: transparent;")
            self.viewport().setAutoFillBackground(False)
            self.setBackgroundBrush(QtGui.QBrush(QtCore.Qt.transparent))
        else:
            self.setBackgroundBrush(QtGui.QColor("#102844"))

        self.map_pixmap = QtGui.QPixmap(str(map_path))
        if self.map_pixmap.isNull():
            raise FileNotFoundError(f"无法加载地图图片: {map_path}")
        self.map_item = self._scene.addPixmap(self.map_pixmap)
        self.map_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self._scene.setSceneRect(0, 0, self.map_pixmap.width(), self.map_pixmap.height())

        self.marker_items = []
        self.points = []
        self.selected_sub_ids = set()
        self.search_text = ""
        self.icon_pixmaps = {}
        self._scale_factor = 1.0
        self._suspend_signals = False
        self.blank_mode = False

        self.player_pose = None
        self.player_candidates = []
        self.tracking_history = []
        self.tracking_path_color = "#ff4b4b"
        self.overlay_text = ""
        self.overlay_match_error = None
        self.overlay_alert_text = ""
        self.overlay_alert_color = "#ff6b6b"
        self.overlay_position = DEFAULT_OVERLAY_POSITION
        self.current_fps = 0.0
        self._last_frame_time = None

    def set_points(self, points: list[dict]):
        self.points = list(points)
        self._rebuild_markers()

    def set_icon_pixmaps(self, icon_pixmaps: dict[str, QtGui.QPixmap]):
        self.icon_pixmaps = dict(icon_pixmaps)
        self._rebuild_markers()

    def set_map_opacity(self, opacity: float):
        self.map_item.setOpacity(max(0.15, min(1.0, float(opacity))))

    def set_transparent_background(self, enabled: bool):
        if enabled:
            self.setStyleSheet("background: transparent; border: none;")
            self.viewport().setStyleSheet("background: transparent;")
            self.viewport().setAutoFillBackground(False)
            self.setBackgroundBrush(QtGui.QBrush(QtCore.Qt.transparent))
        else:
            self.setStyleSheet("")
            self.viewport().setStyleSheet("")
            self.viewport().setAutoFillBackground(True)
            self.setBackgroundBrush(QtGui.QColor("#102844"))

    def set_blank_mode(self, enabled: bool):
        self.blank_mode = bool(enabled)
        self.map_item.setVisible(not self.blank_mode)
        for item in self.marker_items:
            item.setVisible((not self.blank_mode) and item.isVisible())
        self.viewport().update()

    def set_filters(self, selected_sub_ids: set[str], search_text: str):
        self.selected_sub_ids = set(selected_sub_ids)
        self.search_text = (search_text or "").strip().lower()
        self._apply_marker_visibility()

    def set_overlay_text(self, text: str):
        self.overlay_text = text or ""
        self.viewport().update()

    def set_overlay_match_error(self, error_value):
        self.overlay_match_error = error_value
        self.viewport().update()

    def set_overlay_alert(self, text: str = "", color: str = "#ff6b6b"):
        self.overlay_alert_text = text or ""
        self.overlay_alert_color = color
        self.viewport().update()

    def set_overlay_position(self, position: str):
        if position in OVERLAY_POSITION_OPTIONS:
            self.overlay_position = position
            self.viewport().update()

    def set_player_candidates(self, candidates):
        self.player_candidates = list(candidates or [])
        self.viewport().update()

    def clear_tracking_history(self):
        self.tracking_history = []
        self.viewport().update()

    def set_tracking_path_color(self, color_value: str):
        self.tracking_path_color = color_value or "#ff4b4b"
        self.viewport().update()

    def append_tracking_history(self, pose_xy, stamp=None):
        if pose_xy is None:
            return
        stamp = time.time() if stamp is None else float(stamp)
        self.tracking_history.append((stamp, float(pose_xy[0]), float(pose_xy[1])))
        cutoff = stamp - 30.0
        self.tracking_history = [row for row in self.tracking_history if row[0] >= cutoff]
        self.viewport().update()

    def apply_location_result(self, pose_xy, candidates=None, follow_center=False):
        self.player_pose = pose_xy
        self.player_candidates = list(candidates or [])
        if follow_center and pose_xy is not None:
            self.centerOn(float(pose_xy[0]), float(pose_xy[1]))
            self._emit_view_changed()
        self.viewport().update()

    def center_on_point(self, x_map: int, y_map: int):
        self.centerOn(float(x_map), float(y_map))
        self._emit_view_changed()

    def reset_zoom(self, center_x=None, center_y=None):
        if center_x is None or center_y is None:
            center = self.get_view_state()
            center_x = center["center_x"]
            center_y = center["center_y"]
        self.restore_view_state(DEFAULT_VIEW_SCALE, center_x, center_y)

    def restore_view_state(self, scale_value: float, center_x: float, center_y: float):
        self._scale_factor = max(MIN_SCALE, min(MAX_SCALE, float(scale_value)))
        center_x = max(0.0, min(float(self.map_pixmap.width()), float(center_x)))
        center_y = max(0.0, min(float(self.map_pixmap.height()), float(center_y)))
        self._suspend_signals = True
        try:
            self.resetTransform()
            self.scale(self._scale_factor, self._scale_factor)
            self.centerOn(center_x, center_y)
        finally:
            self._suspend_signals = False
        self._emit_view_changed()

    def get_view_state(self):
        viewport_center = self.viewport().rect().center()
        scene_center = self.mapToScene(viewport_center)
        return {
            "scale": float(self._scale_factor),
            "center_x": max(0.0, min(float(self.map_pixmap.width()), float(scene_center.x()))),
            "center_y": max(0.0, min(float(self.map_pixmap.height()), float(scene_center.y()))),
        }

    def zoom_by_factor(self, factor: float):
        new_scale = max(MIN_SCALE, min(MAX_SCALE, self._scale_factor * float(factor)))
        if math.isclose(new_scale, self._scale_factor, rel_tol=0.0, abs_tol=1e-9):
            return
        factor = new_scale / self._scale_factor
        self._scale_factor = new_scale
        self.scale(factor, factor)
        self._emit_view_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._emit_view_changed()

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(dx, dy)
        if dx or dy:
            self._emit_view_changed()

    def wheelEvent(self, event):
        if not self.interactive_navigation:
            event.ignore()
            return
        angle = event.angleDelta().y()
        if angle == 0:
            return super().wheelEvent(event)
        factor = 1.12 if angle > 0 else 1 / 1.12
        self.zoom_by_factor(factor)
        event.accept()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self.blank_mode:
            return
        now = time.perf_counter()
        if self._last_frame_time is not None:
            dt = now - self._last_frame_time
            if dt > 1e-6:
                instant_fps = 1.0 / dt
                if self.current_fps <= 0.0:
                    self.current_fps = instant_fps
                else:
                    self.current_fps = self.current_fps * 0.8 + instant_fps * 0.2
        self._last_frame_time = now
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        if len(self.tracking_history) >= 2:
            path = QtGui.QPainterPath()
            first = self.mapFromScene(QtCore.QPointF(self.tracking_history[0][1], self.tracking_history[0][2]))
            path.moveTo(first)
            for _, x_map, y_map in self.tracking_history[1:]:
                point = self.mapFromScene(QtCore.QPointF(x_map, y_map))
                path.lineTo(point)
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(self.tracking_path_color),
                    3,
                    QtCore.Qt.SolidLine,
                    QtCore.Qt.RoundCap,
                    QtCore.Qt.RoundJoin,
                )
            )
            painter.drawPath(path)

        for candidate in self.player_candidates:
            point = self.mapFromScene(QtCore.QPointF(candidate["x_map"], candidate["y_map"]))
            rank = candidate.get("rank", 0)
            ring_color = QtGui.QColor("#ff9a9a" if rank == 1 else "#ffe27a")
            fill_color = QtGui.QColor("#ff4b4b" if rank == 1 else "#ffb400")
            painter.setPen(QtGui.QPen(ring_color, 2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawEllipse(point, 12, 12)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
            painter.setBrush(fill_color)
            painter.drawEllipse(point, 4, 4)
            painter.setPen(QtGui.QColor("#fff7d6"))
            painter.drawText(point + QtCore.QPoint(15, -12), str(rank))

        if self.player_pose is not None:
            point = self.mapFromScene(QtCore.QPointF(self.player_pose[0], self.player_pose[1]))
            painter.setPen(QtGui.QPen(QtGui.QColor("#ff9a9a"), 2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawEllipse(point, 16, 16)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
            painter.setBrush(QtGui.QColor("#ff4b4b"))
            painter.drawEllipse(point, 8, 8)

        overlay_text = self.overlay_text or ""
        if overlay_text:
            overlay_text = f"{overlay_text}   FPS: {self.current_fps:.1f}"
        else:
            overlay_text = f"FPS: {self.current_fps:.1f}"
        if self.overlay_match_error is not None:
            overlay_text = f"{overlay_text}   误差: {self.overlay_match_error:.3f}".strip()
        if overlay_text:
            viewport_rect = self.viewport().rect()
            flags = {
                "左下": QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom,
                "右下": QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
                "左上": QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                "右上": QtCore.Qt.AlignRight | QtCore.Qt.AlignTop,
                "上方": QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                "下方": QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom,
            }.get(self.overlay_position, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
            painter.setPen(QtGui.QColor("#f4f8ff"))
            painter.drawText(viewport_rect.adjusted(18, 18, -18, -18), int(flags), overlay_text)
            if self.overlay_alert_text:
                alert_rect = viewport_rect.adjusted(18, 42, -18, -18)
                painter.setPen(QtGui.QColor(self.overlay_alert_color))
                painter.drawText(alert_rect, int(flags), self.overlay_alert_text)
        painter.restore()

    def _rebuild_markers(self):
        for item in self.marker_items:
            self._scene.removeItem(item)
        self.marker_items = []
        for point in self.points:
            item = MarkerItem(point, self.icon_pixmaps.get(point["sub_id"]), self.markerSelected.emit)
            self._scene.addItem(item)
            self.marker_items.append(item)
        self._apply_marker_visibility()

    def _apply_marker_visibility(self):
        needle = self.search_text
        selected = self.selected_sub_ids
        for item in self.marker_items:
            point = item.point
            visible = point["sub_id"] in selected
            if visible and needle:
                visible = (
                    needle in point["name"].lower()
                    or needle in point["sub_name"].lower()
                    or needle in point["major_name"].lower()
                )
            item.setVisible(visible and not self.blank_mode)

    def _emit_view_changed(self):
        if not self._suspend_signals:
            self.viewChanged.emit(self.get_view_state())


class OverlayMapWindow(QtWidgets.QWidget):
    def __init__(self, map_path: Path):
        flags = QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
        transparent_input_flag = getattr(QtCore.Qt, "WindowTransparentForInput", 0)
        if transparent_input_flag:
            flags |= transparent_input_flag
        super().__init__(None, flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet("background: transparent;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.map_view = MapGraphicsView(
            map_path,
            self,
            transparent_background=True,
            interactive_navigation=False,
        )
        layout.addWidget(self.map_view)


class DetachedMapWindow(QtWidgets.QWidget):
    geometryChanged = QtCore.pyqtSignal()

    def __init__(self, map_path: Path, topmost: bool = False):
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setWindowTitle("地图窗口")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.map_view = MapGraphicsView(map_path, self, transparent_background=True)
        layout.addWidget(self.map_view)
        self.set_topmost(bool(topmost))

    def set_topmost(self, enabled: bool):
        geometry = self.geometry()
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, bool(enabled))
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, bool(enabled))
        self.map_view.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, bool(enabled))
        self.map_view.set_transparent_background(True)
        self.show()
        if geometry.width() > 0 and geometry.height() > 0:
            self.setGeometry(geometry)
        set_window_clickthrough(self, bool(enabled))

    def moveEvent(self, event):
        super().moveEvent(event)
        self.geometryChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometryChanged.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        validate_inputs()
        self.config = load_qt_config()
        self.majors, self.subcategory_defs, self.points = load_dataset()
        self.selected_sub_ids = load_user_selection()
        valid_sub_ids = {row["sub_id"] for row in self.subcategory_defs}
        self.selected_sub_ids &= valid_sub_ids
        if not self.selected_sub_ids:
            self.selected_sub_ids = set(valid_sub_ids)

        self.original_icon_images = {}
        self.icon_pixmaps = {}
        self.sub_items = {}
        self.major_items = {}
        self._tree_syncing = False

        self.signal_bus = SignalBus()
        self.signal_bus.log_message.connect(self._append_log)
        self.signal_bus.locate_success.connect(self._on_locate_success)
        self.signal_bus.locate_failed.connect(self._on_locate_failed)
        self.signal_bus.tracking_failed.connect(self._on_tracking_frame_failed)
        self.signal_bus.tracking_stopped.connect(self._finish_tracking_stop)

        self.global_search_lock = threading.Lock()
        self.full_scan_active = False
        self.log_queue = queue.Queue()
        self.log_timer = QtCore.QTimer(self)
        self.log_timer.timeout.connect(self._drain_log_queue)
        self.log_timer.start(100)
        self.view_save_timer = QtCore.QTimer(self)
        self.view_save_timer.setSingleShot(True)
        self.view_save_timer.timeout.connect(self._save_config)

        self.matcher = None
        self.minimap_selector = None
        self.map_window = None
        self.minimap_match_status = "未定位"
        self.tracking_active = False
        self.tracking_stop_event = None
        self.tracking_thread = None
        self.tracking_restore_selector = False
        self.tracking_last_pose = None
        self.error_curve_history = []
        self.error_curve_index = 0

        self._load_base_icons()
        self._build_ui()
        self._apply_window_config()
        self._rebuild_icon_pixmaps()
        self._build_matcher()
        self._populate_tree()
        self.map_view.set_points(self.points)
        self.map_view.set_tracking_path_color(PATH_COLORS.get(self.config["path_color"], "#ff4b4b"))
        self._apply_filters()
        self._restore_initial_view()
        self._set_log_visible(bool(self.config.get("log_visible", False)))
        self._set_error_curve_visible(bool(self.config.get("error_curve", False)))
        self._sync_runtime_settings()
        self._update_status()

    def _build_ui(self):
        self.setWindowTitle("洛克王国世界本地地图工具 - Qt")
        self.resize(int(self.config["window_width"]), int(self.config["window_height"]))

        sidebar_scroll = QtWidgets.QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setCentralWidget(sidebar_scroll)

        sidebar = QtWidgets.QWidget()
        sidebar_scroll.setWidget(sidebar)
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        header_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("洛克王国世界本地地图")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #22313f;")
        header_row.addWidget(title)
        header_row.addStretch(1)
        minus_btn = QtWidgets.QPushButton("-")
        plus_btn = QtWidgets.QPushButton("+")
        minus_btn.setFixedWidth(34)
        plus_btn.setFixedWidth(34)
        minus_btn.clicked.connect(lambda: self.map_view.zoom_by_factor(1 / 1.15))
        plus_btn.clicked.connect(lambda: self.map_view.zoom_by_factor(1.15))
        header_row.addWidget(minus_btn)
        header_row.addWidget(plus_btn)
        sidebar_layout.addLayout(header_row)

        actions_group = QtWidgets.QGroupBox("快捷操作")
        actions_layout = QtWidgets.QHBoxLayout(actions_group)
        self.fit_btn = QtWidgets.QPushButton("重置缩放 60%")
        self.selector_btn = QtWidgets.QPushButton("校准小地图")
        self.locate_btn = QtWidgets.QPushButton("截取并定位")
        self.track_btn = QtWidgets.QPushButton("实时追踪")
        self.topmost_check = QtWidgets.QCheckBox("窗口置顶")
        self.topmost_check.setChecked(bool(self.config.get("topmost", False)))
        self.topmost_check.toggled.connect(self._on_topmost_toggled)
        self.fit_btn.clicked.connect(self.fit_map)
        self.selector_btn.clicked.connect(self.open_minimap_selector)
        self.locate_btn.clicked.connect(self.capture_and_locate_player)
        self.track_btn.clicked.connect(self.toggle_tracking)
        for button in (self.fit_btn, self.selector_btn, self.locate_btn, self.track_btn):
            actions_layout.addWidget(button)
        actions_layout.addWidget(self.topmost_check)
        sidebar_layout.addWidget(actions_group)

        settings_group = QtWidgets.QGroupBox()
        settings_group.setTitle("")
        settings_layout = QtWidgets.QGridLayout(settings_group)
        settings_layout.setColumnStretch(1, 1)
        settings_layout.setColumnStretch(3, 1)

        self.icon_size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.icon_size_slider.setRange(12, 48)
        self.icon_size_slider.setValue(int(self.config["icon_size"]))
        self.icon_size_slider.valueChanged.connect(self._on_visual_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("图标大小"), 0, 0)
        settings_layout.addWidget(self.icon_size_slider, 0, 1, 1, 3)

        self.outline_combo = QtWidgets.QComboBox()
        self.outline_combo.addItems(list(OUTLINE_COLORS.keys()))
        self.outline_combo.setCurrentText(str(self.config["outline_color"]))
        self.outline_combo.currentTextChanged.connect(self._on_visual_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("描边颜色"), 1, 0)
        settings_layout.addWidget(self.outline_combo, 1, 1)

        self.path_color_combo = QtWidgets.QComboBox()
        self.path_color_combo.addItems(list(PATH_COLORS.keys()))
        self.path_color_combo.setCurrentText(str(self.config["path_color"]))
        self.path_color_combo.currentTextChanged.connect(self._on_visual_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("路径颜色"), 1, 2)
        settings_layout.addWidget(self.path_color_combo, 1, 3)

        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.opacity_slider.setRange(15, 100)
        self.opacity_slider.setValue(int(self.config["map_opacity"]))
        self.opacity_slider.valueChanged.connect(self._on_map_opacity_change)
        settings_layout.addWidget(QtWidgets.QLabel("地图透明度"), 2, 0)
        settings_layout.addWidget(self.opacity_slider, 2, 1, 1, 3)

        self.map_window_width_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.map_window_width_slider.setRange(360, 4000)
        self.map_window_width_slider.setValue(int(self.config.get("map_window_width", 1200)))
        self.map_window_width_slider.valueChanged.connect(self._on_map_window_size_change)
        settings_layout.addWidget(QtWidgets.QLabel("地图窗宽度"), 3, 0)
        settings_layout.addWidget(self.map_window_width_slider, 3, 1)

        self.map_window_height_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.map_window_height_slider.setRange(260, 3000)
        self.map_window_height_slider.setValue(int(self.config.get("map_window_height", 940)))
        self.map_window_height_slider.valueChanged.connect(self._on_map_window_size_change)
        settings_layout.addWidget(QtWidgets.QLabel("地图窗高度"), 3, 2)
        settings_layout.addWidget(self.map_window_height_slider, 3, 3)

        self.map_window_position_combo = QtWidgets.QComboBox()
        self.map_window_position_combo.addItems(list(MAP_WINDOW_POSITION_OPTIONS))
        self.map_window_position_combo.setCurrentText(str(self.config.get("map_window_position", "右侧")))
        self.map_window_position_combo.currentTextChanged.connect(self._on_map_window_size_change)
        settings_layout.addWidget(QtWidgets.QLabel("地图位置"), 4, 0)
        settings_layout.addWidget(self.map_window_position_combo, 4, 1)

        self.match_map_size_spin = QtWidgets.QSpinBox()
        self.match_map_size_spin.setRange(512, 20000)
        self.match_map_size_spin.setValue(int(self.config["match_map_size"]))
        self.match_map_size_spin.valueChanged.connect(self._on_match_map_size_change)
        settings_layout.addWidget(QtWidgets.QLabel("匹配地图尺寸 1x"), 4, 2)
        settings_layout.addWidget(self.match_map_size_spin, 4, 3)

        self.match_scale_combo = QtWidgets.QComboBox()
        self.match_scale_combo.addItems(list(MATCH_SCALE_OPTIONS))
        self.match_scale_combo.setCurrentText(str(self.config["match_scale"]))
        self.match_scale_combo.currentTextChanged.connect(self._on_match_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("匹配倍率"), 5, 0)
        settings_layout.addWidget(self.match_scale_combo, 5, 1)

        self.match_method_combo = QtWidgets.QComboBox()
        self.match_method_combo.addItems(list(MATCH_METHOD_OPTIONS))
        self.match_method_combo.setCurrentText(str(self.config["match_method"]))
        self.match_method_combo.currentTextChanged.connect(self._on_match_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("匹配方法"), 5, 2)
        settings_layout.addWidget(self.match_method_combo, 5, 3)

        self.overlay_position_combo = QtWidgets.QComboBox()
        self.overlay_position_combo.addItems(list(OVERLAY_POSITION_OPTIONS))
        self.overlay_position_combo.setCurrentText(str(self.config["overlay_position"]))
        self.overlay_position_combo.currentTextChanged.connect(self._on_overlay_position_change)
        settings_layout.addWidget(QtWidgets.QLabel("角落文字位置"), 6, 0)
        settings_layout.addWidget(self.overlay_position_combo, 6, 1)

        self.track_follow_check = QtWidgets.QCheckBox("追踪时地图中心跟随")
        self.track_follow_check.setChecked(bool(self.config["track_follow"]))
        self.track_follow_check.toggled.connect(self._on_track_setting_change)
        settings_layout.addWidget(self.track_follow_check, 6, 2, 1, 2)

        self.track_interval_spin = QtWidgets.QSpinBox()
        self.track_interval_spin.setRange(10, 5000)
        self.track_interval_spin.setValue(int(self.config["track_interval_ms"]))
        self.track_interval_spin.valueChanged.connect(self._on_track_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("追踪间隔 ms"), 7, 0)
        settings_layout.addWidget(self.track_interval_spin, 7, 1)

        self.track_window_spin = QtWidgets.QSpinBox()
        self.track_window_spin.setRange(200, 4000)
        self.track_window_spin.setValue(int(self.config["track_search_window"]))
        self.track_window_spin.valueChanged.connect(self._on_track_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("局部搜索范围"), 7, 2)
        settings_layout.addWidget(self.track_window_spin, 7, 3)

        self.track_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.track_threshold_spin.setRange(0.0, 1.0)
        self.track_threshold_spin.setDecimals(3)
        self.track_threshold_spin.setSingleStep(0.01)
        self.track_threshold_spin.setValue(float(self.config["track_match_threshold"]))
        self.track_threshold_spin.valueChanged.connect(self._on_track_setting_change)
        settings_layout.addWidget(QtWidgets.QLabel("局部匹配阈值"), 8, 0)
        settings_layout.addWidget(self.track_threshold_spin, 8, 1)

        self.minimap_size_spin = QtWidgets.QSpinBox()
        self.minimap_size_spin.setRange(80, 360)
        self.minimap_size_spin.setValue(int(self.config["minimap_size"]))
        self.minimap_size_spin.valueChanged.connect(self._on_minimap_size_change)
        settings_layout.addWidget(QtWidgets.QLabel("校准框大小"), 8, 2)
        settings_layout.addWidget(self.minimap_size_spin, 8, 3)

        self.minimap_left_spin = QtWidgets.QSpinBox()
        self.minimap_left_spin.setRange(-20000, 20000)
        self.minimap_left_spin.setValue(int(self.config["minimap_left"]))
        self.minimap_left_spin.valueChanged.connect(self._on_minimap_position_change)
        settings_layout.addWidget(QtWidgets.QLabel("校准框左上 X"), 9, 0)
        settings_layout.addWidget(self.minimap_left_spin, 9, 1)

        self.minimap_top_spin = QtWidgets.QSpinBox()
        self.minimap_top_spin.setRange(-20000, 20000)
        self.minimap_top_spin.setValue(int(self.config["minimap_top"]))
        self.minimap_top_spin.valueChanged.connect(self._on_minimap_position_change)
        settings_layout.addWidget(QtWidgets.QLabel("校准框左上 Y"), 9, 2)
        settings_layout.addWidget(self.minimap_top_spin, 9, 3)

        self.debug_check = QtWidgets.QCheckBox("调试模式")
        self.debug_check.setChecked(bool(self.config["debug_mode"]))
        self.debug_check.toggled.connect(self._on_misc_setting_change)
        settings_layout.addWidget(self.debug_check, 10, 0, 1, 2)

        self.error_curve_check = QtWidgets.QCheckBox("误差曲线")
        self.error_curve_check.setChecked(bool(self.config["error_curve"]))
        self.error_curve_check.toggled.connect(self._on_error_curve_toggle)
        settings_layout.addWidget(self.error_curve_check, 10, 2, 1, 2)

        self.log_check = QtWidgets.QCheckBox("运行日志")
        self.log_check.setChecked(bool(self.config["log_visible"]))
        self.log_check.toggled.connect(self._on_log_toggle)
        settings_layout.addWidget(self.log_check, 11, 0, 1, 2)

        self.tools_section = CollapsibleSection("工具设置", bool(self.config.get("tools_expanded", False)))
        self.tools_section.setContent(settings_group)
        self.tools_section.toggled.connect(lambda _checked: self._save_config_debounced())
        sidebar_layout.addWidget(self.tools_section)

        marker_group = QtWidgets.QGroupBox()
        marker_group.setTitle("")
        marker_layout = QtWidgets.QVBoxLayout(marker_group)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("搜索分类或点位名称")
        self.search_edit.textChanged.connect(self._apply_filters)
        marker_layout.addWidget(self.search_edit)
        top_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("全选")
        clear_btn = QtWidgets.QPushButton("清空")
        select_all_btn.clicked.connect(self.select_all_subcategories)
        clear_btn.clicked.connect(self.clear_all_subcategories)
        top_row.addWidget(select_all_btn)
        top_row.addWidget(clear_btn)
        top_row.addStretch(1)
        marker_layout.addLayout(top_row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.setIconSize(QtCore.QSize(28, 28))
        marker_layout.addWidget(self.tree)
        self.markers_section = CollapsibleSection("标点筛选", bool(self.config.get("markers_expanded", False)))
        self.markers_section.setContent(marker_group)
        self.markers_section.toggled.connect(lambda _checked: self._save_config_debounced())
        sidebar_layout.addWidget(self.markers_section, 1)

        detail_group = QtWidgets.QGroupBox()
        detail_group.setTitle("")
        detail_layout = QtWidgets.QVBoxLayout(detail_group)
        self.detail_label = QtWidgets.QLabel("点击地图上的标点后，这里会显示名称、分类和坐标。")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        detail_layout.addWidget(self.detail_label)
        self.detail_section = CollapsibleSection("点位详情", bool(self.config.get("detail_expanded", False)))
        self.detail_section.setContent(detail_group)
        self.detail_section.toggled.connect(lambda _checked: self._save_config_debounced())
        sidebar_layout.addWidget(self.detail_section)

        status_group = QtWidgets.QGroupBox()
        status_group.setTitle("")
        status_layout = QtWidgets.QVBoxLayout(status_group)
        self.status_text = QtWidgets.QLabel()
        self.status_text.setWordWrap(True)
        self.status_text.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        status_layout.addWidget(self.status_text)
        self.status_section = CollapsibleSection("状态", bool(self.config.get("status_expanded", False)))
        self.status_section.setContent(status_group)
        self.status_section.toggled.connect(lambda _checked: self._save_config_debounced())
        sidebar_layout.addWidget(self.status_section)
        sidebar_layout.addStretch(1)

        self.map_window = DetachedMapWindow(MAP_IMAGE_PATH, topmost=bool(self.config.get("topmost", False)))
        self.map_window.geometryChanged.connect(self._save_config_debounced)
        self.map_view = self.map_window.map_view
        self.map_view.markerSelected.connect(self.show_marker_detail)
        self.map_view.viewChanged.connect(self._on_view_changed)

        self.log_dock = QtWidgets.QDockWidget("运行日志", self)
        self.log_dock.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_dock.setWidget(self.log_text)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.log_dock)

        self.error_curve_dock = QtWidgets.QDockWidget("误差曲线", self)
        self.error_curve_dock.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.error_curve_widget = ErrorCurveWidget()
        self.error_curve_dock.setWidget(self.error_curve_widget)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.error_curve_dock)
        self.tabifyDockWidget(self.log_dock, self.error_curve_dock)
        self.log_dock.raise_()

    def _apply_window_config(self):
        x = self.config.get("window_x")
        y = self.config.get("window_y")
        if isinstance(x, int) and isinstance(y, int):
            self.move(x, y)
        if self.map_window is not None:
            self.map_window.resize(
                int(self.config.get("map_window_width", 1200)),
                int(self.config.get("map_window_height", 940)),
            )
            self.map_window.set_topmost(bool(self.topmost_check.isChecked()))
            self._sync_map_window_geometry()
            self.map_window.show()

    def _sync_map_window_geometry(self):
        if self.map_window is None:
            return
        width = max(360, int(self.map_window_width_slider.value()))
        height = max(260, int(self.map_window_height_slider.value()))
        position = self.map_window_position_combo.currentText()
        if position == "左侧":
            x = int(self.x() - width - MAP_WINDOW_GAP)
            y = int(self.y())
        elif position == "上方":
            x = int(self.x())
            y = int(self.y() - height - MAP_WINDOW_GAP)
        elif position == "下方":
            x = int(self.x())
            y = int(self.y() + self.height() + MAP_WINDOW_GAP)
        else:
            x = int(self.x() + self.width() + MAP_WINDOW_GAP)
            y = int(self.y())
        self.map_window.setGeometry(x, y, width, height)

    def _load_base_icons(self):
        for sub in self.subcategory_defs:
            image_file = sub.get("image_file", "")
            image_path = DATA_DIR / image_file if image_file else None
            if image_path and image_path.exists():
                self.original_icon_images[sub["sub_id"]] = Image.open(image_path).convert("RGBA")
        if not self.original_icon_images:
            self.original_icon_images["__fallback__"] = Image.new("RGBA", (18, 18), (255, 200, 0, 255))

    def _rebuild_icon_pixmaps(self):
        outline_color = OUTLINE_COLORS.get(self.outline_combo.currentText(), "#ffffff")
        icon_size = int(self.icon_size_slider.value())
        self.icon_pixmaps = {}
        fallback = None
        for sub in self.subcategory_defs:
            base = self.original_icon_images.get(sub["sub_id"]) or self.original_icon_images.get("__fallback__")
            if base is None:
                continue
            icon_pixmap = pil_to_pixmap(render_icon_with_outline(base, icon_size, outline_color))
            self.icon_pixmaps[sub["sub_id"]] = icon_pixmap
            if fallback is None:
                fallback = icon_pixmap
        if fallback is not None:
            for sub in self.subcategory_defs:
                self.icon_pixmaps.setdefault(sub["sub_id"], fallback)
        self.map_view.set_icon_pixmaps(self.icon_pixmaps)
        self.tree.setIconSize(QtCore.QSize(icon_size + 8, icon_size + 8))
        for sub_id, item in self.sub_items.items():
            pixmap = self.icon_pixmaps.get(sub_id)
            if pixmap is not None:
                item.setIcon(0, QtGui.QIcon(pixmap))

    def _build_matcher(self):
        self.matcher = MiniMapMatcher(MAP_IMAGE_PATH, int(self.match_map_size_spin.value()))

    def _populate_tree(self):
        self._tree_syncing = True
        try:
            self.tree.clear()
            self.sub_items = {}
            self.major_items = {}
            grouped = {}
            for sub in self.subcategory_defs:
                grouped.setdefault(sub["major_id"], {"name": sub["major_name"], "subs": []})
                grouped[sub["major_id"]]["subs"].append(sub)
            for major_id in sorted(grouped):
                meta = grouped[major_id]
                total = sum(row["count"] for row in meta["subs"])
                major_item = QtWidgets.QTreeWidgetItem([f"{meta['name']} ({total})"])
                major_item.setFlags(major_item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsTristate)
                self.tree.addTopLevelItem(major_item)
                self.major_items[major_id] = major_item
                for sub in meta["subs"]:
                    child = QtWidgets.QTreeWidgetItem([f"{sub['sub_name']} ({sub['count']})"])
                    child.setFlags(child.flags() | QtCore.Qt.ItemIsUserCheckable)
                    child.setData(0, QtCore.Qt.UserRole, sub["sub_id"])
                    child.setCheckState(0, QtCore.Qt.Checked if sub["sub_id"] in self.selected_sub_ids else QtCore.Qt.Unchecked)
                    pixmap = self.icon_pixmaps.get(sub["sub_id"])
                    if pixmap is not None:
                        child.setIcon(0, QtGui.QIcon(pixmap))
                    major_item.addChild(child)
                    self.sub_items[sub["sub_id"]] = child
                major_item.setExpanded(True)
        finally:
            self._tree_syncing = False

    def _restore_initial_view(self):
        center_x = self.config.get("view_center_x")
        center_y = self.config.get("view_center_y")
        if center_x is None or center_y is None:
            center_x = self.map_view.map_pixmap.width() / 2
            center_y = self.map_view.map_pixmap.height() / 2
        self.map_view.restore_view_state(float(self.config["view_scale"]), float(center_x), float(center_y))
        self.map_view.set_map_opacity(self.opacity_slider.value() / 100.0)
        self.map_view.set_overlay_position(self.overlay_position_combo.currentText())

    def _sync_runtime_settings(self):
        self.track_follow = bool(self.track_follow_check.isChecked())
        self.track_interval_ms = int(self.track_interval_spin.value())
        self.track_search_window = int(self.track_window_spin.value())
        self.track_match_threshold = float(self.track_threshold_spin.value())
        self.match_scale_label = str(self.match_scale_combo.currentText())
        self.match_method_label = str(self.match_method_combo.currentText())
        self.debug_mode = bool(self.debug_check.isChecked())
        self.minimap_left = int(self.minimap_left_spin.value())
        self.minimap_top = int(self.minimap_top_spin.value())
        self.minimap_size = int(self.minimap_size_spin.value())

    def _on_map_window_size_change(self, _value=None):
        self._sync_map_window_geometry()
        self._save_config_debounced()
        self._update_status()

    def _append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def _drain_log_queue(self):
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.appendPlainText("\n".join(lines))
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _set_log_visible(self, visible: bool):
        self.log_dock.setVisible(bool(visible))
        self.log_check.blockSignals(True)
        self.log_check.setChecked(bool(visible))
        self.log_check.blockSignals(False)

    def _set_error_curve_visible(self, visible: bool):
        self.error_curve_dock.setVisible(bool(visible))
        self.error_curve_check.blockSignals(True)
        self.error_curve_check.setChecked(bool(visible))
        self.error_curve_check.blockSignals(False)

    def _ensure_overlay_window(self):
        return

    def _destroy_overlay_window(self):
        return

    def _sync_overlay_window(self):
        return

    def _visible_marker_count(self):
        needle = self.search_edit.text().strip().lower()
        count = 0
        for point in self.points:
            if point["sub_id"] not in self.selected_sub_ids:
                continue
            if needle and not (
                needle in point["name"].lower()
                or needle in point["sub_name"].lower()
                or needle in point["major_name"].lower()
            ):
                continue
            count += 1
        return count

    def _apply_filters(self):
        needle = self.search_edit.text().strip().lower()
        self.map_view.set_filters(self.selected_sub_ids, needle)
        sub_by_id = {row["sub_id"]: row for row in self.subcategory_defs}
        for major_item in self.major_items.values():
            visible_children = 0
            for index in range(major_item.childCount()):
                child = major_item.child(index)
                sub = sub_by_id[child.data(0, QtCore.Qt.UserRole)]
                matches_name = not needle or needle in sub["sub_name"].lower() or needle in sub["major_name"].lower()
                matches_item = False
                if needle:
                    matches_item = any(needle in point["name"].lower() for point in self.points if point["sub_id"] == sub["sub_id"])
                child_visible = matches_name or matches_item or not needle
                child.setHidden(not child_visible)
                if child_visible:
                    visible_children += 1
            major_item.setHidden(visible_children == 0)
        self._update_status()
        self._save_config_debounced()

    def fit_map(self):
        self.map_view.reset_zoom()
        self._update_status()
        self._save_config_debounced()

    def _on_visual_setting_change(self, _value=None):
        self._rebuild_icon_pixmaps()
        self.map_view.set_tracking_path_color(PATH_COLORS.get(self.path_color_combo.currentText(), "#ff4b4b"))
        self._save_config_debounced()
        self._update_status()

    def _on_map_opacity_change(self, _value=None):
        self.map_view.set_map_opacity(self.opacity_slider.value() / 100.0)
        self._save_config_debounced()
        self._update_status()

    def _on_match_map_size_change(self, _value=None):
        self._build_matcher()
        self._append_log(f"匹配地图尺寸(1x): {self.match_map_size_spin.value()}")
        self._save_config_debounced()
        self._update_status()

    def _on_match_setting_change(self, _value=None):
        self._sync_runtime_settings()
        self._save_config_debounced()
        self._update_status()

    def _on_overlay_position_change(self, _value=None):
        self.map_view.set_overlay_position(self.overlay_position_combo.currentText())
        self._save_config_debounced()
        self._update_status()

    def _on_track_setting_change(self, _value=None):
        self._sync_runtime_settings()
        self._save_config_debounced()
        self._update_status()

    def _on_minimap_size_change(self, _value=None):
        self._sync_runtime_settings()
        if self.minimap_selector is not None:
            self.minimap_selector.set_selector_geometry(self.minimap_left, self.minimap_top, self.minimap_size)
        self._save_config_debounced()
        self._update_status()

    def _on_minimap_position_change(self, _value=None):
        self._sync_runtime_settings()
        if self.minimap_selector is not None:
            self.minimap_selector.set_selector_geometry(self.minimap_left, self.minimap_top, self.minimap_size)
        self._save_config_debounced()
        self._update_status()

    def _on_misc_setting_change(self, _checked=None):
        self._sync_runtime_settings()
        self._save_config_debounced()
        self._update_status()

    def _on_error_curve_toggle(self, checked: bool):
        self._set_error_curve_visible(bool(checked))
        self._save_config_debounced()
        self._update_status()

    def _on_log_toggle(self, checked: bool):
        self._set_log_visible(bool(checked))
        self._save_config_debounced()
        self._update_status()

    def _on_topmost_toggled(self, enabled: bool):
        if self.map_window is not None:
            self.map_window.set_topmost(bool(enabled))
        self._save_config_debounced()
        self._update_status()

    def _on_tree_item_changed(self, item, _column):
        if self._tree_syncing:
            return
        sub_id = item.data(0, QtCore.Qt.UserRole)
        if not sub_id:
            self.selected_sub_ids = {
                row["sub_id"]
                for row in self.subcategory_defs
                if self.sub_items[row["sub_id"]].checkState(0) == QtCore.Qt.Checked
            }
        else:
            if item.checkState(0) == QtCore.Qt.Checked:
                self.selected_sub_ids.add(sub_id)
            else:
                self.selected_sub_ids.discard(sub_id)
        save_user_selection(self.selected_sub_ids)
        self._apply_filters()

    def select_all_subcategories(self):
        self._tree_syncing = True
        try:
            self.selected_sub_ids = {row["sub_id"] for row in self.subcategory_defs}
            for item in self.sub_items.values():
                item.setCheckState(0, QtCore.Qt.Checked)
        finally:
            self._tree_syncing = False
        save_user_selection(self.selected_sub_ids)
        self._apply_filters()

    def clear_all_subcategories(self):
        self._tree_syncing = True
        try:
            self.selected_sub_ids.clear()
            for item in self.sub_items.values():
                item.setCheckState(0, QtCore.Qt.Unchecked)
        finally:
            self._tree_syncing = False
        save_user_selection(self.selected_sub_ids)
        self._apply_filters()

    def show_marker_detail(self, point: dict):
        self.detail_label.setText(
            "\n".join(
                [
                    f"名称: {point['name']}",
                    f"分类: {point['major_name']} / {point['sub_name']}",
                    f"编号: {point['id']}",
                    f"像素坐标: ({point['x_map']}, {point['y_map']})",
                    f"来源: {point['source']}",
                ]
            )
        )
        self.map_view.center_on_point(point["x_map"], point["y_map"])

    def _on_view_changed(self, _view_state: dict):
        self._update_status()
        self._save_config_debounced()

    def _save_config_debounced(self):
        self.view_save_timer.start(250)

    def _save_config(self):
        geometry = self.geometry()
        view_state = self.map_view.get_view_state()
        map_geometry = self.map_window.geometry() if self.map_window is not None else QtCore.QRect()
        self.config.update(
            {
                "window_width": int(geometry.width()),
                "window_height": int(geometry.height()),
                "window_x": int(geometry.x()),
                "window_y": int(geometry.y()),
                "sidebar_width": int(self.width()),
                "map_window_width": int(self.map_window_width_slider.value()),
                "map_window_height": int(self.map_window_height_slider.value()),
                "map_window_position": str(self.map_window_position_combo.currentText()),
                "map_window_x": int(map_geometry.x()),
                "map_window_y": int(map_geometry.y()),
                "icon_size": int(self.icon_size_slider.value()),
                "map_opacity": int(self.opacity_slider.value()),
                "outline_color": str(self.outline_combo.currentText()),
                "path_color": str(self.path_color_combo.currentText()),
                "view_scale": float(view_state["scale"]),
                "view_center_x": float(view_state["center_x"]),
                "view_center_y": float(view_state["center_y"]),
                "match_map_size": int(self.match_map_size_spin.value()),
                "match_scale": str(self.match_scale_combo.currentText()),
                "match_method": str(self.match_method_combo.currentText()),
                "overlay_position": str(self.overlay_position_combo.currentText()),
                "topmost": bool(self.topmost_check.isChecked()),
                "track_follow": bool(self.track_follow_check.isChecked()),
                "track_interval_ms": int(self.track_interval_spin.value()),
                "track_search_window": int(self.track_window_spin.value()),
                "track_match_threshold": float(self.track_threshold_spin.value()),
                "minimap_left": int(self.minimap_left_spin.value()),
                "minimap_top": int(self.minimap_top_spin.value()),
                "minimap_size": int(self.minimap_size_spin.value()),
                "debug_mode": bool(self.debug_check.isChecked()),
                "error_curve": bool(self.error_curve_check.isChecked()),
                "log_visible": bool(self.log_check.isChecked()),
                "tools_expanded": bool(self.tools_section.isExpanded()),
                "markers_expanded": bool(self.markers_section.isExpanded()),
                "detail_expanded": bool(self.detail_section.isExpanded()),
                "status_expanded": bool(self.status_section.isExpanded()),
            }
        )
        save_qt_config(self.config)

    def _get_minimap_bbox(self):
        return (
            int(self.minimap_left_spin.value()),
            int(self.minimap_top_spin.value()),
            int(self.minimap_left_spin.value()) + int(self.minimap_size_spin.value()),
            int(self.minimap_top_spin.value()) + int(self.minimap_size_spin.value()),
        )

    def _on_selector_changed(self, x: int, y: int, size: int):
        self.minimap_left_spin.blockSignals(True)
        self.minimap_top_spin.blockSignals(True)
        self.minimap_size_spin.blockSignals(True)
        self.minimap_left_spin.setValue(int(x))
        self.minimap_top_spin.setValue(int(y))
        self.minimap_size_spin.setValue(int(size))
        self.minimap_left_spin.blockSignals(False)
        self.minimap_top_spin.blockSignals(False)
        self.minimap_size_spin.blockSignals(False)
        self._sync_runtime_settings()
        self._save_config_debounced()
        if self.debug_mode:
            self._update_status()

    def open_minimap_selector(self):
        if self.minimap_selector is None:
            self.minimap_selector = MinimapSelectorWindow(
                self.minimap_size_spin.value(),
                self.minimap_left_spin.value(),
                self.minimap_top_spin.value(),
            )
            self.minimap_selector.changed.connect(self._on_selector_changed)
        self.minimap_selector.set_selector_geometry(
            self.minimap_left_spin.value(),
            self.minimap_top_spin.value(),
            self.minimap_size_spin.value(),
        )
        self.minimap_selector.show()
        self.minimap_match_status = "已打开校准框"
        self._append_log(
            f"打开校准框: 左上角=({self.minimap_left_spin.value()}, {self.minimap_top_spin.value()}), 大小={self.minimap_size_spin.value()}"
        )
        self._update_status()

    def _capture_minimap(self, bbox, log=True):
        capture = ImageGrab.grab(bbox=bbox).convert("RGB")
        if log:
            self._append_log(f"截图完成: 尺寸={capture.size}")
        return capture

    def capture_and_locate_player(self):
        if self.tracking_active:
            self.minimap_match_status = "请先停止实时追踪"
            self._update_status()
            return
        bbox = self._get_minimap_bbox()
        match_scale_label = str(self.match_scale_combo.currentText())
        selector_was_visible = bool(self.minimap_selector is not None and self.minimap_selector.isVisible())
        self._append_log(f"开始定位: 截图区域={bbox}")
        self.minimap_match_status = "定位中..."
        self._update_status()

        def worker():
            if selector_was_visible and self.minimap_selector is not None:
                QtCore.QMetaObject.invokeMethod(self.minimap_selector, "hide", QtCore.Qt.QueuedConnection)
            self._run_locate_attempt(bbox, match_scale_label, selector_was_visible, False)

        threading.Thread(target=worker, daemon=True).start()

    def _run_locate_attempt(self, bbox, match_scale_label: str, selector_was_visible: bool, track_mode: bool):
        attempt_started = time.perf_counter()
        try:
            capture = self._capture_minimap(bbox)
        except Exception as exc:
            self._append_log(f"截图失败: {exc}")
            if track_mode:
                self.signal_bus.tracking_failed.emit(f"实时追踪截图失败: {exc}")
            else:
                self.signal_bus.locate_failed.emit(f"截图失败: {exc}", selector_was_visible)
            return None

        stamp = time.strftime("%Y%m%d_%H%M%S")
        if self.debug_mode and not track_mode:
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            capture_path = TEMP_DIR / f"minimap_capture_{stamp}.png"
            capture.save(capture_path)
            self._append_log(f"调试截图已保存: {capture_path}")

        self._append_log("开始全图匹配...")
        with self.global_search_lock:
            self.full_scan_active = True
            try:
                result = self.matcher.match(
                    capture,
                    scale_label=match_scale_label,
                    method_label=self.match_method_label,
                    progress_cb=self._append_log,
                )
            finally:
                self.full_scan_active = False
        if not result:
            self._append_log("匹配失败: 未找到结果")
            if track_mode:
                self.signal_bus.tracking_failed.emit("实时追踪未找到匹配位置")
            else:
                self.signal_bus.locate_failed.emit("未找到匹配位置", selector_was_visible)
            return None

        self._append_log(
            f"匹配完成: scope={result.get('search_scope', 'full')}, x={result['x_map']}, y={result['y_map']}, "
            f"score={result['score']:.3f}, method={result['match_method']}, match={result['match_elapsed_ms']:.2f}ms, "
            f"total={result['total_elapsed_ms']:.2f}ms"
        )
        if self.debug_mode and not track_mode:
            image_paths = self.matcher.save_debug_images(capture, result, TEMP_DIR, stamp)
            if image_paths:
                self._append_log(f"调试匹配图片已保存: {', '.join(image_paths.values())}")
        callback_queued_at = time.perf_counter()
        self.signal_bus.locate_success.emit(result, selector_was_visible, track_mode, attempt_started, callback_queued_at)
        return result

    def toggle_tracking(self):
        if self.tracking_active:
            self.stop_tracking("已停止实时追踪")
        else:
            self.start_tracking()

    def start_tracking(self):
        if self.tracking_active:
            return
        self._sync_runtime_settings()
        bbox = self._get_minimap_bbox()
        full_search_scale_label = str(self.match_scale_combo.currentText())
        selector_was_visible = bool(self.minimap_selector is not None and self.minimap_selector.isVisible())
        self.tracking_active = True
        self.tracking_restore_selector = selector_was_visible
        self.tracking_stop_event = threading.Event()
        stop_event = self.tracking_stop_event
        self.tracking_last_pose = None
        self.map_view.clear_tracking_history()
        self.track_btn.setText("停止追踪")
        self.map_view.set_player_candidates([])
        self.minimap_match_status = "实时追踪中..."
        self._update_status()
        self._append_log(
            f"开始实时追踪: 截图区域={bbox}, 全图倍率={full_search_scale_label}, 局部范围={self.track_search_window}px, 阈值={self.track_match_threshold:.3f}"
        )
        if selector_was_visible and self.minimap_selector is not None:
            self.minimap_selector.hide()

        def worker():
            while not stop_event.is_set():
                cycle_started = time.perf_counter()
                self._run_tracking_cycle(bbox, full_search_scale_label)
                remaining = self.track_interval_ms / 1000.0 - (time.perf_counter() - cycle_started)
                if remaining > 0 and stop_event.wait(remaining):
                    break

        self.tracking_thread = threading.Thread(target=worker, daemon=True)
        self.tracking_thread.start()

    def stop_tracking(self, message=None):
        if self.tracking_stop_event is not None:
            self.tracking_stop_event.set()
        self.tracking_active = False
        self.tracking_thread = None
        self.tracking_stop_event = None
        self.tracking_last_pose = None
        self.map_view.clear_tracking_history()
        self.track_btn.setText("实时追踪")
        if self.tracking_restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        self.tracking_restore_selector = False
        if message:
            self.minimap_match_status = message
            self._update_status()

    def _run_tracking_cycle(self, bbox, full_search_scale_label: str):
        try:
            capture = self._capture_minimap(bbox, log=self.debug_mode)
        except Exception as exc:
            self._append_log(f"截图失败: {exc}")
            self.signal_bus.tracking_failed.emit(f"实时追踪截图失败: {exc}")
            return None

        result = None
        if self.tracking_last_pose is not None:
            result = self.matcher.match(
                capture,
                scale_label="1x",
                method_label=self.match_method_label,
                local_center_map=self.tracking_last_pose,
                local_window_map=self.track_search_window,
                progress_cb=self._append_log if self.debug_mode else None,
            )
            if result is None or result["score"] > self.track_match_threshold:
                self.signal_bus.tracking_failed.emit("失锁")
                result = None
        if result is None:
            if self.full_scan_active or not self.global_search_lock.acquire(blocking=False):
                self._append_log("全图扫描跳过: 当前已有全图扫描进行中")
                self.signal_bus.tracking_failed.emit("失锁")
                return None
            try:
                self.full_scan_active = True
                result = self.matcher.match(
                    capture,
                    scale_label=full_search_scale_label,
                    method_label=self.match_method_label,
                    progress_cb=self._append_log if self.debug_mode else None,
                )
            finally:
                self.full_scan_active = False
                self.global_search_lock.release()
            if not result:
                self.signal_bus.tracking_failed.emit("失锁")
                return None
        self.tracking_last_pose = (result["x_map"], result["y_map"])
        self.signal_bus.locate_success.emit(result, False, True, None, time.perf_counter())
        return result

    @QtCore.pyqtSlot(str, bool)
    def _on_locate_failed(self, message: str, restore_selector=False):
        if restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        self.map_view.apply_location_result(None, candidates=[], follow_center=False)
        self.map_view.set_overlay_match_error(None)
        self.minimap_match_status = message
        self._update_status()

    @QtCore.pyqtSlot(str)
    def _on_tracking_frame_failed(self, message: str):
        if self.tracking_active:
            self.tracking_last_pose = None
            self.map_view.set_overlay_alert("失锁", "#ff4b4b")
            self.minimap_match_status = message
            self._update_status()

    @QtCore.pyqtSlot(object, bool, bool, object, object)
    def _on_locate_success(self, result: dict, restore_selector=False, track_mode=False, attempt_started=None, callback_queued_at=None):
        if track_mode and not self.tracking_active:
            return
        if restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        if callback_queued_at is not None:
            self._append_log(f"UI 排队等待: {(time.perf_counter() - callback_queued_at) * 1000.0:.2f}ms")
        if track_mode:
            self.map_view.apply_location_result((result["x_map"], result["y_map"]), candidates=[], follow_center=self.track_follow)
            if result.get("search_scope") == "local":
                self.map_view.append_tracking_history((result["x_map"], result["y_map"]))
            self.map_view.set_overlay_alert("", "#ff4b4b")
        else:
            self.map_view.apply_location_result((result["x_map"], result["y_map"]), candidates=result.get("top_matches", []), follow_center=False)
            self.map_view.center_on_point(result["x_map"], result["y_map"])
            self.map_view.set_overlay_alert("", "#ff4b4b")
        self.map_view.set_overlay_match_error(result.get("score"))
        self._append_error_curve_result(result)
        top_scores = " | ".join(f"{row['rank']}:{row['score']:.3f}" for row in result.get("top_matches", [])[:5])
        self.minimap_match_status = (
            f"实时追踪: ({result['x_map']}, {result['y_map']}) score={result['score']:.3f}"
            if track_mode
            else f"定位完成: ({result['x_map']}, {result['y_map']}) score={result['score']:.3f} [Top: {top_scores}]"
        )
        self._update_status()

    def _append_error_curve_result(self, result: dict):
        top_matches = result.get("top_matches", [])[:3]
        row = [float(item.get("score", 0.0)) for item in top_matches]
        while len(row) < 3:
            row.append(None)
        self.error_curve_index += 1
        self.error_curve_history.append((self.error_curve_index, row))
        if len(self.error_curve_history) > 180:
            self.error_curve_history = self.error_curve_history[-180:]
        self.error_curve_widget.set_history(self.error_curve_history)

    @QtCore.pyqtSlot(str)
    def _finish_tracking_stop(self, message: str):
        self.stop_tracking(message)

    def _update_status(self):
        self._sync_runtime_settings()
        state = self.map_view.get_view_state()
        self.map_view.set_overlay_text(f"缩放: {state['scale'] * 100:.0f}%   点位: {self._visible_marker_count()}   透明度: {self.opacity_slider.value()}%")
        self.status_text.setText(
            "\n".join(
                [
                    f"数据目录: {DATA_DIR}",
                    f"已加载大类: {len(self.majors)}",
                    f"已加载点位: {len(self.points)}",
                    f"当前选中子类: {len(self.selected_sub_ids)} / {len(self.subcategory_defs)}",
                    f"当前显示点位: {self._visible_marker_count()} / {len(self.points)}",
                    f"图标大小: {self.icon_size_slider.value()}   透明度: {self.opacity_slider.value()}%   匹配倍率: {self.match_scale_combo.currentText()}   匹配地图: {self.match_map_size_spin.value()}   匹配方法: {self.match_method_combo.currentText()}",
                    f"追踪跟随: {'开' if self.track_follow else '关'}   间隔: {self.track_interval_ms}ms   范围: {self.track_search_window}px   阈值: {self.track_match_threshold:.3f}",
                    f"小地图定位: {self.minimap_match_status}",
                ]
            )
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_map_window_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_map_window_geometry()

    def closeEvent(self, event):
        self.stop_tracking()
        if self.minimap_selector is not None:
            self.minimap_selector.close()
        if self.map_window is not None:
            self.map_window.close()
        self._save_config()
        super().closeEvent(event)


def run(smoke_test: bool = False):
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("desktop-map-tool-qt")
    window = MainWindow()
    window.show()
    if smoke_test:
        QtCore.QTimer.singleShot(1200, window.close)
    return app.exec_()


def main():
    parser = argparse.ArgumentParser(description="洛克王国世界本地地图工具 - Qt")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(smoke_test=args.smoke_test))


if __name__ == "__main__":
    main()
