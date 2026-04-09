import argparse
import ctypes
import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageOps, ImageTk

Image.MAX_IMAGE_PIXELS = None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MAP_IMAGE_PATH = DATA_DIR / "map_8192.png"
IMAGE_DIR = DATA_DIR / "image"
TEMP_DIR = BASE_DIR / "temp"
USER_DATA_DIR = BASE_DIR / "user_data"
USER_SELECTION_PATH = USER_DATA_DIR / "selection_state.json"
CONFIG_PATH = BASE_DIR / "app_config.json"
MIN_SCALE = 0.08
MAX_SCALE = 2.5
LOD_SIZES = (4096, 2048, 1024)
DEFAULT_ICON_SIZE = 22
DEFAULT_MAP_OPACITY = 0.7
DEFAULT_MINIMAP_SIZE = 220
DEFAULT_MINIMAP_LEFT = 2255
DEFAULT_MINIMAP_TOP = 89
MATCH_REFERENCE_SIZE = 278
MATCH_TOP_K = 10
MATCH_CAPTURE_SIZE = DEFAULT_MINIMAP_SIZE
MATCH_INNER_DIAMETER = 50
MATCH_SCALE_OPTIONS = ("0.25x", "0.5x", "1x")
MATCH_SCALE_VALUES = {
    "0.25x": 0.25,
    "0.5x": 0.5,
    "1x": 1.0,
}
DEFAULT_TRACK_INTERVAL_MS = 50
DEFAULT_TRACK_SEARCH_WINDOW = 800
DEFAULT_TRACK_MATCH_THRESHOLD = 0.08
RGB_REFINEMENT_WEIGHT = 0.35
RGB_REFINEMENT_CANDIDATES = 24
CACHE_MAX_PIXELS = 20_000_000
BORDER_IGNORE_RGB = (27, 34, 52)
OVERLAY_POSITION_OPTIONS = ("左下", "右下", "左上", "右上", "上方", "下方")
DEFAULT_OVERLAY_POSITION = "左下"
DEFAULT_OVERLAY_WINDOW_WIDTH = 1100
DEFAULT_OVERLAY_WINDOW_HEIGHT = 900
MATCH_FEATURE_SPECS = (
    ("gray", 0.5),
    ("edge", 0.3),
    ("gradient", 0.2),
)
OUTLINE_COLORS = {
    "白色": "#ffffff",
    "黑色": "#101010",
    "黄色": "#ffd84d",
    "青色": "#7ee7ff",
    "红色": "#ff6b6b",
}

MAJOR_COLORS = {
    "地点": "#6fb4ff",
    "可刷新采集物": "#63c174",
    "一次性采集物": "#f1b84b",
}


def get_match_map_size(source_map_size: int, capture_size: int):
    return int(round(source_map_size / MATCH_REFERENCE_SIZE * capture_size))


def get_match_map_path(source_map_size: int, capture_size: int):
    return DATA_DIR / f"map_match_{get_match_map_size(source_map_size, capture_size)}.png"


def ensure_match_map(source_path: Path, capture_size: int):
    with Image.open(source_path) as src:
        source_size = src.size[0]
        target_size = get_match_map_size(source_size, capture_size)
        target_path = get_match_map_path(source_size, capture_size)
        rebuild = True
        if target_path.exists():
            try:
                with Image.open(target_path) as existing:
                    rebuild = existing.size != (target_size, target_size) or target_path.stat().st_mtime < source_path.stat().st_mtime
            except Exception:
                rebuild = True
        if rebuild:
            resized = src.convert("RGB").resize((target_size, target_size), Image.LANCZOS)
            resized.save(target_path)
    return target_path, target_size


def set_window_clickthrough(toplevel: tk.Toplevel, enabled: bool):
    try:
        hwnd = ctypes.windll.user32.GetParent(toplevel.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


def load_major_files():
    files = sorted(
        [path for path in DATA_DIR.glob("*.json") if path.name != "_meta.json"],
        key=lambda path: path.name,
    )
    majors = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_filename"] = path.name
        majors.append(payload)
    return majors


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, background="#f4efe2", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.interior = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.interior.bind("<Configure>", self._on_interior_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)
        self.interior.bind("<Enter>", self._bind_mousewheel)
        self.interior.bind("<Leave>", self._unbind_mousewheel)

    def _on_interior_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self, _event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_mousewheel_linux(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(3, "units")


class MiniMapSelector:
    def __init__(self, root: tk.Tk, on_change=None):
        self.root = root
        self.on_change = on_change
        self.size = DEFAULT_MINIMAP_SIZE
        self.x = DEFAULT_MINIMAP_LEFT
        self.y = DEFAULT_MINIMAP_TOP
        self.drag_offset = (0, 0)

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#ff00ff")
        try:
            self.win.wm_attributes("-transparentcolor", "#ff00ff")
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.win, bg="#ff00ff", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self._redraw()

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def hide(self):
        self.win.withdraw()

    def geometry_box(self):
        return self.x, self.y, self.x + self.size, self.y + self.size

    def top_right(self):
        return self.x + self.size, self.y

    def bottom_left(self):
        return self.x, self.y + self.size

    def top_left(self):
        return self.x, self.y

    def bottom_right(self):
        return self.x + self.size, self.y + self.size

    def set_position(self, x: int, y: int):
        self.x = int(x)
        self.y = int(y)
        self.win.geometry(f"{self.size}x{self.size}+{self.x}+{self.y}")
        if self.on_change:
            self.on_change(self.x, self.y, self.size)

    def _on_press(self, event):
        self.drag_offset = (event.x, event.y)

    def _on_drag(self, event):
        pointer_x = self.win.winfo_pointerx()
        pointer_y = self.win.winfo_pointery()
        self.x = pointer_x - self.drag_offset[0]
        self.y = pointer_y - self.drag_offset[1]
        self.win.geometry(f"{self.size}x{self.size}+{self.x}+{self.y}")
        if self.on_change:
            self.on_change(self.x, self.y, self.size)

    def set_size(self, size: int):
        size = max(80, min(360, int(size)))
        if size == self.size:
            return
        self.size = size
        self.win.geometry(f"{self.size}x{self.size}+{self.x}+{self.y}")
        self._redraw()
        if self.on_change:
            self.on_change(self.x, self.y, self.size)

    def _redraw(self):
        self.canvas.config(width=self.size, height=self.size)
        self.canvas.delete("all")
        pad = 3
        self.canvas.create_oval(
            pad,
            pad,
            self.size - pad,
            self.size - pad,
            outline="#00e6ff",
            width=3,
        )


class MiniMapMatcher:
    def __init__(self, map_image_path: Path):
        self.map_image_path = map_image_path
        self.full_map = Image.open(map_image_path).convert("RGB")
        self.top_k = MATCH_TOP_K
        self.mask_cache = {}
        self.scale_contexts = {}

    def _preprocess(self, image: Image.Image):
        gray = image.convert("L")
        gray = gray.filter(ImageFilter.GaussianBlur(1))
        return np.asarray(gray, dtype=np.uint8)

    def _build_feature_map(self, gray_arr: np.ndarray, feature_name: str):
        if feature_name == "gray":
            return gray_arr
        if feature_name == "edge":
            return cv2.Canny(gray_arr, 40, 120)
        if feature_name == "gradient":
            grad_x = cv2.Sobel(gray_arr, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray_arr, cv2.CV_32F, 0, 1, ksize=3)
            magnitude = cv2.magnitude(grad_x, grad_y)
            return cv2.convertScaleAbs(magnitude)
        raise ValueError(f"Unsupported feature detector: {feature_name}")

    def _circle_mask(self, size: int):
        yy, xx = np.ogrid[:size, :size]
        cx = cy = size / 2.0
        outer_radius = size / 2.0
        inner_radius = size * (MATCH_INNER_DIAMETER / DEFAULT_MINIMAP_SIZE) / 2.0
        dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
        ring = (dist2 <= outer_radius**2) & (dist2 >= inner_radius**2)
        return np.where(ring, 255, 0).astype(np.uint8)

    def _get_mask(self, size: int):
        if size not in self.mask_cache:
            self.mask_cache[size] = self._circle_mask(size)
        return self.mask_cache[size]

    def _get_scale_context(self, capture_size: int):
        if capture_size not in self.scale_contexts:
            match_map_path, scaled_map_size = ensure_match_map(self.map_image_path, capture_size)
            scaled_map = Image.open(match_map_path).convert("RGB")
            scaled_rgb = np.asarray(scaled_map, dtype=np.uint8)
            self.scale_contexts[capture_size] = {
                "match_map_path": match_map_path,
                "scaled_map_size": scaled_map_size,
                "map_rgb_arr": scaled_rgb,
            }
        return self.scale_contexts[capture_size]

    def _build_search_region(self, scaled_map_size: int, template_size: int, local_center_map=None, local_window_map=None):
        if not local_center_map or not local_window_map:
            return None
        window_scaled = max(
            template_size + 4,
            int(round(local_window_map / self.full_map.size[0] * scaled_map_size)),
        )
        half_window = window_scaled / 2.0
        center_x = local_center_map[0] / self.full_map.size[0] * scaled_map_size
        center_y = local_center_map[1] / self.full_map.size[1] * scaled_map_size
        left = int(round(center_x - half_window))
        top = int(round(center_y - half_window))
        right = left + window_scaled
        bottom = top + window_scaled
        if left < 0:
            right -= left
            left = 0
        if top < 0:
            bottom -= top
            top = 0
        if right > scaled_map_size:
            shift = right - scaled_map_size
            left = max(0, left - shift)
            right = scaled_map_size
        if bottom > scaled_map_size:
            shift = bottom - scaled_map_size
            top = max(0, top - shift)
            bottom = scaled_map_size
        if right - left < template_size or bottom - top < template_size:
            return None
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "window_map": int(local_window_map),
        }

    def _scaled_to_map(self, left: int, top: int, score: float, rank: int, template_size: int, scaled_map_size: int):
        center_x_scaled = left + template_size / 2.0
        center_y_scaled = top + template_size / 2.0
        return {
            "rank": rank,
            "x_map": int(round(center_x_scaled / scaled_map_size * self.full_map.size[0])),
            "y_map": int(round(center_y_scaled / scaled_map_size * self.full_map.size[1])),
            "score": max(0.0, float(score)),
            "match_left": int(left),
            "match_top": int(top),
        }

    def _extract_top_candidates(
        self,
        result_map: np.ndarray,
        template_size: int,
        scaled_map_size: int,
        left_offset: int = 0,
        top_offset: int = 0,
    ):
        working = result_map.copy()
        candidates = []
        suppress_radius = max(12, template_size // 2)
        for rank in range(1, self.top_k + 1):
            min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(working)
            if not np.isfinite(min_val):
                break
            left, top = min_loc
            left += left_offset
            top += top_offset
            candidates.append(self._scaled_to_map(left, top, min_val, rank, template_size, scaled_map_size))
            local_left = left - left_offset
            local_top = top - top_offset
            x0 = max(0, local_left - suppress_radius)
            y0 = max(0, local_top - suppress_radius)
            x1 = min(working.shape[1], local_left + suppress_radius + 1)
            y1 = min(working.shape[0], local_top + suppress_radius + 1)
            working[y0:y1, x0:x1] = np.inf
        return candidates

    def _sanitize_result_map(self, result_map: np.ndarray):
        finite_mask = np.isfinite(result_map)
        if not finite_mask.any():
            return None
        finite_values = result_map[finite_mask]
        worst_value = float(finite_values.max())
        cleaned = np.where(finite_mask, result_map, worst_value).astype(np.float32, copy=False)
        return cleaned

    def _run_feature_detection(
        self,
        feature_name: str,
        map_arr: np.ndarray,
        template_arr: np.ndarray,
        match_mask: np.ndarray,
        template_size: int,
        scaled_map_size: int,
        left_offset: int = 0,
        top_offset: int = 0,
    ):
        started = time.perf_counter()
        result_map = cv2.matchTemplate(
            map_arr,
            template_arr,
            cv2.TM_SQDIFF_NORMED,
            mask=match_mask,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result_map = self._sanitize_result_map(result_map)
        if result_map is None:
            return None
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result_map)
        left, top = min_loc
        left += left_offset
        top += top_offset
        best = self._scaled_to_map(left, top, min_val, 1, template_size, scaled_map_size)
        return {
            "name": feature_name,
            "elapsed_ms": round(elapsed_ms, 2),
            "score": float(min_val),
            "x_map": best["x_map"],
            "y_map": best["y_map"],
            "match_left": best["match_left"],
            "match_top": best["match_top"],
            "result_map": result_map,
        }

    def _score_rgb_candidate(self, candidate: dict, template_rgb: np.ndarray, map_rgb_arr: np.ndarray, match_mask: np.ndarray):
        size = template_rgb.shape[0]
        left = candidate["match_left"]
        top = candidate["match_top"]
        crop = map_rgb_arr[top : top + size, left : left + size]
        if crop.shape[:2] != template_rgb.shape[:2]:
            return None
        diff = np.abs(crop.astype(np.float32) - template_rgb.astype(np.float32)).mean(axis=2) / 255.0
        mask_bool = match_mask > 0
        if not mask_bool.any():
            return None
        return float(diff[mask_bool].mean())

    def _refine_candidates_with_rgb(self, candidates: list[dict], template_rgb: np.ndarray, map_rgb_arr: np.ndarray, match_mask: np.ndarray):
        if not candidates:
            return []
        rgb_scores = []
        valid_scores = []
        for candidate in candidates:
            rgb_score = self._score_rgb_candidate(candidate, template_rgb, map_rgb_arr, match_mask)
            rgb_scores.append(rgb_score)
            if rgb_score is not None and np.isfinite(rgb_score):
                valid_scores.append(rgb_score)
        if not valid_scores:
            return candidates
        min_rgb = min(valid_scores)
        max_rgb = max(valid_scores)
        rgb_span = max(1e-9, max_rgb - min_rgb)
        refined = []
        for candidate, rgb_score in zip(candidates, rgb_scores):
            rgb_score = max_rgb if rgb_score is None or not np.isfinite(rgb_score) else rgb_score
            combined = (1.0 - RGB_REFINEMENT_WEIGHT) * candidate["score"] + RGB_REFINEMENT_WEIGHT * ((rgb_score - min_rgb) / rgb_span)
            updated = dict(candidate)
            updated["feature_score"] = candidate["score"]
            updated["rgb_score"] = rgb_score
            updated["score"] = max(0.0, float(combined))
            refined.append(updated)
        refined.sort(key=lambda row: row["score"])
        for rank, row in enumerate(refined, 1):
            row["rank"] = rank
        return refined

    def match(
        self,
        capture: Image.Image,
        scale_label: str = "1x",
        local_center_map=None,
        local_window_map=None,
        progress_cb=None,
    ):
        overall_started = time.perf_counter()
        if capture.width != capture.height:
            return None
        source_capture_size = min(capture.size)
        if source_capture_size != MATCH_CAPTURE_SIZE:
            capture = capture.resize((MATCH_CAPTURE_SIZE, MATCH_CAPTURE_SIZE), Image.LANCZOS)
        scale_factor = MATCH_SCALE_VALUES.get(scale_label, 1.0)
        target_capture_size = max(24, int(round(min(capture.size) * scale_factor)))
        if target_capture_size != capture.width:
            capture = capture.resize((target_capture_size, target_capture_size), Image.LANCZOS)
        capture_size = min(capture.size)
        scale_context = self._get_scale_context(capture_size)
        capture_rgb = np.asarray(capture.convert("RGB"), dtype=np.uint8)
        search_region = self._build_search_region(
            scale_context["scaled_map_size"],
            capture_size,
            local_center_map=local_center_map,
            local_window_map=local_window_map,
        )
        search_scope = "local" if search_region else "full"
        if search_region:
            map_rgb_arr = scale_context["map_rgb_arr"][
                search_region["top"] : search_region["bottom"],
                search_region["left"] : search_region["right"],
            ]
            left_offset = search_region["left"]
            top_offset = search_region["top"]
        else:
            map_rgb_arr = scale_context["map_rgb_arr"]
            left_offset = 0
            top_offset = 0
        map_shape = map_rgb_arr.shape
        if capture_rgb.shape[0] > map_shape[0] or capture_rgb.shape[1] > map_shape[1]:
            return None
        match_mask = self._get_mask(capture_size)
        if progress_cb:
            search_text = (
                f"局部搜索 {search_region['window_map']}x{search_region['window_map']}px"
                if search_region
                else "全图搜索"
            )
            progress_cb(
                f"匹配流程: {search_text} -> 原始截图 {source_capture_size}px -> 归一化 {MATCH_CAPTURE_SIZE}px -> "
                f"倍率 {scale_label} -> 模板 {capture_size}px -> 匹配地图 {scale_context['scaled_map_size']}px "
                f"({scale_context['match_map_path'].name})"
            )

        rgb_started = time.perf_counter()
        result_map = cv2.matchTemplate(
            map_rgb_arr,
            capture_rgb,
            cv2.TM_SQDIFF_NORMED,
            mask=match_mask,
        )
        rgb_elapsed_ms = (time.perf_counter() - rgb_started) * 1000.0
        result_map = self._sanitize_result_map(result_map)
        if result_map is None:
            return None
        if progress_cb:
            min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result_map)
            probe = self._scaled_to_map(
                min_loc[0] + left_offset,
                min_loc[1] + top_offset,
                min_val,
                1,
                capture_size,
                scale_context["scaled_map_size"],
            )
            progress_cb(
                f"RGB score={min_val:.4f} time={rgb_elapsed_ms:.2f}ms "
                f"pos=({probe['x_map']}, {probe['y_map']})"
            )

        top_matches = self._extract_top_candidates(
            result_map,
            capture_size,
            scale_context["scaled_map_size"],
            left_offset=left_offset,
            top_offset=top_offset,
        )
        if not top_matches:
            return None
        best = top_matches[0]
        total_elapsed_ms = (time.perf_counter() - overall_started) * 1000.0
        return {
            "x_map": best["x_map"],
            "y_map": best["y_map"],
            "score": best["score"],
            "source_capture_size": source_capture_size,
            "normalized_capture_size": MATCH_CAPTURE_SIZE,
            "capture_size": capture_size,
            "template_size": capture_size,
            "scaled_map_size": scale_context["scaled_map_size"],
            "match_map_path": str(scale_context["match_map_path"]),
            "match_left": best["match_left"],
            "match_top": best["match_top"],
            "top_matches": top_matches,
            "feature_results": [],
            "rgb_elapsed_ms": round(rgb_elapsed_ms, 2),
            "total_elapsed_ms": round(total_elapsed_ms, 2),
            "match_method": "rgb_only",
            "match_scale_label": scale_label,
            "match_scale_factor": scale_factor,
            "search_scope": search_scope,
            "search_window_map": int(local_window_map) if search_region and local_window_map else None,
            "rgb_refinement_weight": 1.0,
        }


class MapCanvas(ttk.Frame):
    def __init__(
        self,
        master,
        map_image_path: Path,
        points: list[dict],
        icon_cache: dict[str, ImageTk.PhotoImage],
        icon_pil_cache: dict[str, Image.Image],
        on_marker_select,
        on_view_change=None,
        render_base_map: bool = True,
        render_points: bool = True,
        render_runtime_markers: bool = True,
        render_overlay_ui: bool = True,
        interactive_navigation: bool = True,
        transparent_bg: bool = False,
    ):
        super().__init__(master)
        self.points = points
        self.icon_cache = icon_cache
        self.icon_pil_cache = icon_pil_cache
        self.on_marker_select = on_marker_select
        self.on_view_change = on_view_change
        self.render_base_map = render_base_map
        self.render_points = render_points
        self.render_runtime_markers = render_runtime_markers
        self.render_overlay_ui = render_overlay_ui
        self.interactive_navigation = interactive_navigation
        self.transparent_bg = transparent_bg

        self.full_image = Image.open(map_image_path).convert("RGBA")
        self.map_width, self.map_height = self.full_image.size
        self.image_pyramid = self._build_image_pyramid()
        self.current_lod_size = self.map_width

        canvas_bg = "#ff00ff" if transparent_bg else "#102844"
        self.canvas = tk.Canvas(self, background=canvas_bg, highlightthickness=0, cursor="fleur" if interactive_navigation else "arrow")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        if interactive_navigation:
            self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
            self.canvas.bind("<B1-Motion>", self._on_drag_move)
            self.canvas.bind("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind("<Button-4>", self._on_mousewheel_linux)
            self.canvas.bind("<Button-5>", self._on_mousewheel_linux)

        self.scale = 0.12
        self.min_scale = MIN_SCALE
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.last_offset_x = 0.0
        self.last_offset_y = 0.0
        self.viewport_width = 1
        self.viewport_height = 1
        self.search_text = ""
        self.selected_sub_ids: set[str] = set()
        self.overlay_text = ""
        self._base_map_image = None
        self._suspend_view_callback = False
        self.map_opacity = DEFAULT_MAP_OPACITY
        self.player_pose = None
        self.player_candidates = []
        self.current_fps = 0.0
        self._last_redraw_time = None
        self.overlay_match_error = None
        self.overlay_alert_text = ""
        self.overlay_alert_color = "#ff6b6b"
        self.overlay_position = DEFAULT_OVERLAY_POSITION
        self._composited_map_image = None
        self._composited_map_pil = None
        self._composited_map_key = None
        self._composited_map_size = (0, 0)

    def _build_image_pyramid(self):
        pyramid = {self.map_width: self.full_image}
        for size in LOD_SIZES:
            if size == self.map_width:
                continue
            pyramid[size] = self.full_image.resize((size, size), Image.LANCZOS)
        return pyramid

    def _pick_lod_size(self):
        if self.scale <= 0.25:
            return 1024
        if self.scale <= 0.6:
            return 2048
        return 4096

    def fit_to_view(self):
        if self.viewport_width <= 1 or self.viewport_height <= 1:
            return
        scale_x = self.viewport_width / self.map_width
        scale_y = self.viewport_height / self.map_height
        fitted_scale = max(MIN_SCALE, min(MAX_SCALE, max(scale_x, scale_y)))
        self.min_scale = fitted_scale
        self.scale = fitted_scale
        self.offset_x = (self.viewport_width - self.map_width * self.scale) / 2
        self.offset_y = (self.viewport_height - self.map_height * self.scale) / 2
        self._clamp_offset()
        self._invalidate_composited_map()
        self.redraw()

    def set_filters(self, selected_sub_ids: set[str], search_text: str):
        self.selected_sub_ids = set(selected_sub_ids)
        self.search_text = search_text.strip().lower()
        self._invalidate_composited_map()
        self.redraw()

    def set_overlay_text(self, text: str):
        if self.overlay_text == text:
            return
        self.overlay_text = text
        self._suspend_view_callback = True
        try:
            self.redraw()
        finally:
            self._suspend_view_callback = False

    def set_overlay_position(self, position: str):
        if position not in OVERLAY_POSITION_OPTIONS:
            position = DEFAULT_OVERLAY_POSITION
        if self.overlay_position == position:
            return
        self.overlay_position = position
        self.redraw()

    def set_overlay_match_error(self, error_value):
        self.overlay_match_error = error_value
        self.redraw()

    def set_overlay_alert(self, text: str = "", color: str = "#ff6b6b"):
        self.overlay_alert_text = text
        self.overlay_alert_color = color
        self.redraw()

    def set_icon_cache(self, icon_cache: dict[str, ImageTk.PhotoImage], icon_pil_cache: dict[str, Image.Image]):
        self.icon_cache = icon_cache
        self.icon_pil_cache = icon_pil_cache
        self._invalidate_composited_map()
        self.redraw()

    def set_map_opacity(self, opacity: float):
        opacity = max(0.15, min(1.0, opacity))
        if abs(self.map_opacity - opacity) < 1e-6:
            return
        self.map_opacity = opacity
        self._invalidate_composited_map()
        self.redraw()

    def set_player_pose(self, pose_xy):
        self.player_pose = pose_xy
        self.redraw()

    def _invalidate_composited_map(self):
        self._composited_map_image = None
        self._composited_map_pil = None
        self._composited_map_key = None
        self._composited_map_size = (0, 0)

    def _visible_points(self):
        needle = self.search_text
        visible = []
        for point in self.points:
            if point["sub_id"] not in self.selected_sub_ids:
                continue
            if needle and needle not in point["name"].lower() and needle not in point["sub_name"].lower() and needle not in point["major_name"].lower():
                continue
            visible.append(point)
        return visible

    def _build_composited_map(self):
        if not self.render_base_map:
            return False
        scaled_width = max(1, int(round(self.map_width * self.scale)))
        scaled_height = max(1, int(round(self.map_height * self.scale)))
        if scaled_width * scaled_height > CACHE_MAX_PIXELS:
            self._invalidate_composited_map()
            return False
        lod_size = self._pick_lod_size()
        self.current_lod_size = lod_size
        visible_points = self._visible_points() if self.render_points else []
        cache_key = (
            lod_size,
            scaled_width,
            scaled_height,
            round(self.scale, 6),
            round(self.map_opacity, 4),
            tuple(sorted(self.selected_sub_ids)),
            self.search_text,
            tuple((point["id"], point["x_map"], point["y_map"], point["sub_id"]) for point in visible_points),
            tuple(sorted(self.icon_pil_cache.keys())),
        )
        if self._composited_map_key == cache_key and self._composited_map_image is not None:
            return True

        source_image = self.image_pyramid[lod_size]
        composited = source_image.resize((scaled_width, scaled_height), Image.LANCZOS)
        if self.map_opacity < 0.999:
            alpha = composited.getchannel("A").point(lambda a: int(a * self.map_opacity))
            composited.putalpha(alpha)

        for point in visible_points:
            icon = self.icon_pil_cache.get(point["sub_id"])
            px = int(round(point["x_map"] * self.scale))
            py = int(round(point["y_map"] * self.scale))
            if icon is not None:
                left = px - icon.width // 2
                top = py - icon.height // 2
                composited.paste(icon, (left, top), icon)
            else:
                draw = ImageDraw.Draw(composited)
                radius = 5
                color = point["color"]
                draw.ellipse(
                    (px - radius, py - radius, px + radius, py + radius),
                    fill=color,
                    outline="#ffffff",
                    width=1,
                )

        self._composited_map_pil = composited
        self._composited_map_image = ImageTk.PhotoImage(composited)
        self._composited_map_key = cache_key
        self._composited_map_size = (scaled_width, scaled_height)
        return True

    def set_player_candidates(self, candidates):
        self.player_candidates = list(candidates or [])
        self.redraw()

    def apply_location_result(self, pose_xy, candidates=None, follow_center=False):
        self.player_pose = pose_xy
        self.player_candidates = list(candidates or [])
        if follow_center and pose_xy is not None:
            self.offset_x = self.viewport_width / 2 - pose_xy[0] * self.scale
            self.offset_y = self.viewport_height / 2 - pose_xy[1] * self.scale
            self._clamp_offset()
        self.redraw()

    def center_on_map_point(self, x_map: int, y_map: int):
        self.offset_x = self.viewport_width / 2 - x_map * self.scale
        self.offset_y = self.viewport_height / 2 - y_map * self.scale
        self._clamp_offset()
        self.redraw()

    def copy_view_from(self, other):
        self.scale = other.scale
        self.min_scale = other.min_scale
        self.offset_x = other.offset_x
        self.offset_y = other.offset_y
        self.viewport_width = other.viewport_width
        self.viewport_height = other.viewport_height
        self._invalidate_composited_map()
        self.redraw()

    def get_view_state(self):
        center_x = (self.viewport_width / 2 - self.offset_x) / self.scale
        center_y = (self.viewport_height / 2 - self.offset_y) / self.scale
        center_x = max(0.0, min(float(self.map_width), float(center_x)))
        center_y = max(0.0, min(float(self.map_height), float(center_y)))
        return {
            "scale": float(self.scale),
            "center_x": float(center_x),
            "center_y": float(center_y),
        }

    def restore_view_state(self, scale_value: float, center_x: float, center_y: float):
        if self.viewport_width <= 1 or self.viewport_height <= 1:
            return
        scale_value = max(self.min_scale, min(MAX_SCALE, float(scale_value)))
        center_x = max(0.0, min(float(self.map_width), float(center_x)))
        center_y = max(0.0, min(float(self.map_height), float(center_y)))
        self.scale = scale_value
        self.offset_x = self.viewport_width / 2 - center_x * self.scale
        self.offset_y = self.viewport_height / 2 - center_y * self.scale
        self._clamp_offset()
        self._invalidate_composited_map()
        self.redraw()

    def _on_resize(self, event):
        self.viewport_width = max(1, event.width)
        self.viewport_height = max(1, event.height)
        fitted_scale = max(
            MIN_SCALE,
            min(MAX_SCALE, max(self.viewport_width / self.map_width, self.viewport_height / self.map_height)),
        )
        self.min_scale = fitted_scale
        if self._base_map_image is None:
            self.fit_to_view()
        else:
            if self.scale < self.min_scale:
                self.scale = self.min_scale
                self._invalidate_composited_map()
            self._clamp_offset()
            self.redraw()

    def _on_drag_start(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.last_offset_x = self.offset_x
        self.last_offset_y = self.offset_y

    def _on_drag_move(self, event):
        self.offset_x = self.last_offset_x + (event.x - self.drag_start_x)
        self.offset_y = self.last_offset_y + (event.y - self.drag_start_y)
        self._clamp_offset()
        self.redraw()

    def _on_mousewheel(self, event):
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self._zoom_at(event.x, event.y, factor)

    def _on_mousewheel_linux(self, event):
        factor = 1.12 if event.num == 4 else 1 / 1.12
        self._zoom_at(event.x, event.y, factor)

    def _zoom_at(self, canvas_x: float, canvas_y: float, factor: float):
        new_scale = max(self.min_scale, min(MAX_SCALE, self.scale * factor))
        if abs(new_scale - self.scale) < 1e-9:
            return
        world_x = (canvas_x - self.offset_x) / self.scale
        world_y = (canvas_y - self.offset_y) / self.scale
        self.scale = new_scale
        self.offset_x = canvas_x - world_x * self.scale
        self.offset_y = canvas_y - world_y * self.scale
        self._clamp_offset()
        self._invalidate_composited_map()
        self.redraw()

    def zoom_by_factor(self, factor: float):
        self._zoom_at(self.viewport_width / 2, self.viewport_height / 2, factor)

    def _clamp_offset(self):
        scaled_width = self.map_width * self.scale
        scaled_height = self.map_height * self.scale
        if scaled_width <= self.viewport_width:
            self.offset_x = (self.viewport_width - scaled_width) / 2
        else:
            min_x = self.viewport_width - scaled_width
            self.offset_x = min(0, max(min_x, self.offset_x))
        if scaled_height <= self.viewport_height:
            self.offset_y = (self.viewport_height - scaled_height) / 2
        else:
            min_y = self.viewport_height - scaled_height
            self.offset_y = min(0, max(min_y, self.offset_y))

    def redraw(self):
        if self.viewport_width <= 1 or self.viewport_height <= 1:
            return
        now = time.perf_counter()
        if self._last_redraw_time is not None:
            dt = now - self._last_redraw_time
            if dt > 1e-6:
                instant_fps = 1.0 / dt
                if self.current_fps <= 0.0:
                    self.current_fps = instant_fps
                else:
                    self.current_fps = self.current_fps * 0.8 + instant_fps * 0.2
        self._last_redraw_time = now

        self.canvas.delete("all")
        visible_points = self._visible_points() if self.render_points else []
        composited_ready = self._build_composited_map()
        if composited_ready:
            self.canvas.create_image(self.offset_x, self.offset_y, image=self._composited_map_image, anchor="nw")
        elif self.render_base_map:
            crop_left = max(0.0, -self.offset_x / self.scale)
            crop_top = max(0.0, -self.offset_y / self.scale)
            crop_right = min(self.map_width, crop_left + self.viewport_width / self.scale)
            crop_bottom = min(self.map_height, crop_top + self.viewport_height / self.scale)
            crop_box = (
                int(crop_left),
                int(crop_top),
                max(int(crop_left) + 1, int(crop_right)),
                max(int(crop_top) + 1, int(crop_bottom)),
            )

            lod_size = self._pick_lod_size()
            self.current_lod_size = lod_size
            source_image = self.image_pyramid[lod_size]
            lod_ratio = lod_size / self.map_width
            lod_crop_box = (
                int(crop_box[0] * lod_ratio),
                int(crop_box[1] * lod_ratio),
                max(int(crop_box[0] * lod_ratio) + 1, int(crop_box[2] * lod_ratio)),
                max(int(crop_box[1] * lod_ratio) + 1, int(crop_box[3] * lod_ratio)),
            )
            cropped = source_image.crop(lod_crop_box)
            display_width = max(1, int((crop_box[2] - crop_box[0]) * self.scale))
            display_height = max(1, int((crop_box[3] - crop_box[1]) * self.scale))
            rendered = cropped.resize((display_width, display_height), Image.LANCZOS)
            if self.map_opacity < 0.999:
                alpha = rendered.getchannel("A").point(lambda a: int(a * self.map_opacity))
                rendered.putalpha(alpha)
            self._base_map_image = ImageTk.PhotoImage(rendered)
            image_x = crop_box[0] * self.scale + self.offset_x
            image_y = crop_box[1] * self.scale + self.offset_y
            self.canvas.create_image(image_x, image_y, image=self._base_map_image, anchor="nw")

        if self.render_points and not composited_ready:
            for point in visible_points:
                canvas_x = self.offset_x + point["x_map"] * self.scale
                canvas_y = self.offset_y + point["y_map"] * self.scale
                if canvas_x < -24 or canvas_x > self.viewport_width + 24 or canvas_y < -24 or canvas_y > self.viewport_height + 24:
                    continue
                icon = self.icon_cache.get(point["sub_id"])
                if icon is not None:
                    marker_id = self.canvas.create_image(canvas_x, canvas_y, image=icon, anchor="center")
                else:
                    radius = 5
                    color = point["color"]
                    marker_id = self.canvas.create_oval(
                        canvas_x - radius,
                        canvas_y - radius,
                        canvas_x + radius,
                        canvas_y + radius,
                        fill=color,
                        outline="#ffffff",
                        width=1.2,
                    )
                self.canvas.tag_bind(marker_id, "<Button-1>", lambda _event, data=point: self.on_marker_select(data))
                self.canvas.tag_bind(marker_id, "<Enter>", lambda _event: self.canvas.config(cursor="hand2"))
                self.canvas.tag_bind(marker_id, "<Leave>", lambda _event: self.canvas.config(cursor="arrow" if not self.interactive_navigation else "fleur"))

        if self.render_overlay_ui:
            if self.overlay_position == "左下":
                info_x = 74
                overlay_y = self.viewport_height - 18
                info_anchor = "sw"
            elif self.overlay_position == "右下":
                info_x = self.viewport_width - 18
                overlay_y = self.viewport_height - 18
                info_anchor = "se"
            elif self.overlay_position == "左上":
                info_x = 18
                overlay_y = 18
                info_anchor = "nw"
            elif self.overlay_position == "右上":
                info_x = self.viewport_width - 18
                overlay_y = 18
                info_anchor = "ne"
            elif self.overlay_position == "上方":
                info_x = self.viewport_width / 2
                overlay_y = 18
                info_anchor = "n"
            else:
                info_x = self.viewport_width / 2
                overlay_y = self.viewport_height - 18
                info_anchor = "s"

            overlay_text = self.overlay_text
            if overlay_text:
                overlay_text = f"{overlay_text}   FPS: {self.current_fps:.1f}"
            else:
                overlay_text = f"FPS: {self.current_fps:.1f}"
            if self.overlay_match_error is not None:
                overlay_text = f"{overlay_text}   误差: {self.overlay_match_error:.3f}"
            info_id = self.canvas.create_text(info_x, overlay_y, text=overlay_text, fill="#f4f8ff", font=("Microsoft YaHei UI", 10), anchor=info_anchor, tags=("zoom_info",))
        if self.overlay_alert_text:
            alert_y = overlay_y - 18 if "下" in self.overlay_position else overlay_y + 18
            self.canvas.create_text(
                info_x,
                alert_y,
                text=self.overlay_alert_text,
                fill=self.overlay_alert_color,
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor=info_anchor,
                tags=("zoom_alert",),
            )

        if self.render_runtime_markers and self.player_pose is not None:
            px = self.offset_x + self.player_pose[0] * self.scale
            py = self.offset_y + self.player_pose[1] * self.scale
            self.canvas.create_oval(px - 8, py - 8, px + 8, py + 8, fill="#ff4b4b", outline="#ffffff", width=2)
            self.canvas.create_oval(px - 16, py - 16, px + 16, py + 16, outline="#ff9a9a", width=2)

        if self.render_runtime_markers:
            for candidate in self.player_candidates:
                cx = self.offset_x + candidate["x_map"] * self.scale
                cy = self.offset_y + candidate["y_map"] * self.scale
                if cx < -40 or cx > self.viewport_width + 40 or cy < -40 or cy > self.viewport_height + 40:
                    continue
                rank = candidate["rank"]
                if rank == 1:
                    ring_color = "#ff9a9a"
                    fill_color = "#ff4b4b"
                else:
                    ring_color = "#ffe27a"
                    fill_color = "#ffb400"
                self.canvas.create_oval(cx - 12, cy - 12, cx + 12, cy + 12, outline=ring_color, width=2)
                self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=fill_color, outline="#ffffff", width=1)
                self.canvas.create_text(
                    cx + 15,
                    cy - 14,
                    text=str(rank),
                    fill="#fff7d6",
                    font=("Microsoft YaHei UI", 9, "bold"),
                    anchor="sw",
                )

        if self.on_view_change and not self._suspend_view_callback:
            self.on_view_change()


class DesktopMapApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("洛克王国世界本地地图工具")
        self.root.geometry("1660x940")
        self.root.minsize(360, 260)

        self.majors = load_major_files()
        self.points = []
        self.major_vars = {}
        self.sub_vars = {}
        self.sub_rows = {}
        self.major_frames = {}
        self.major_bodies = {}
        self.major_expanded = {}
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.detail_var = tk.StringVar(value="点击地图上的点位后，这里会显示标题、分类和坐标。")
        self.current_visible_marker_count = 0
        self.sub_icon_cache = {}
        self.sub_icon_pil_cache = {}
        self.sub_icon_labels = {}
        self.original_sub_icon_images = {}
        self.icon_size_var = tk.IntVar(value=DEFAULT_ICON_SIZE)
        self.map_opacity_var = tk.IntVar(value=int(DEFAULT_MAP_OPACITY * 100))
        self.outline_color_name_var = tk.StringVar(value="白色")
        self.minimap_size_var = tk.IntVar(value=DEFAULT_MINIMAP_SIZE)
        self.minimap_left_var = tk.IntVar(value=DEFAULT_MINIMAP_LEFT)
        self.minimap_top_var = tk.IntVar(value=DEFAULT_MINIMAP_TOP)
        self.match_scale_var = tk.StringVar(value="1x")
        self.overlay_mode_var = tk.BooleanVar(value=False)
        self.track_follow_var = tk.BooleanVar(value=True)
        self.track_interval_ms_var = tk.IntVar(value=DEFAULT_TRACK_INTERVAL_MS)
        self.track_search_window_var = tk.IntVar(value=DEFAULT_TRACK_SEARCH_WINDOW)
        self.track_match_threshold_var = tk.DoubleVar(value=DEFAULT_TRACK_MATCH_THRESHOLD)
        self.debug_mode_var = tk.BooleanVar(value=False)
        self.tools_expanded = tk.BooleanVar(value=False)
        self.status_expanded = tk.BooleanVar(value=False)
        self.detail_expanded = tk.BooleanVar(value=False)
        self.log_expanded = tk.BooleanVar(value=False)
        self.minimap_selector = None
        self.minimap_match_status = "未定位"
        self.matcher = None
        self.log_queue = queue.Queue()
        self.log_panel_width = 320
        self.log_window = None
        self.log_text = None
        self.overlay_base_window = None
        self.overlay_marker_window = None
        self.overlay_base_canvas = None
        self.overlay_marker_canvas = None
        self.tracking_active = False
        self.tracking_stop_event = None
        self.tracking_thread = None
        self.tracking_restore_selector = False
        self.track_interval_ms = DEFAULT_TRACK_INTERVAL_MS
        self.track_search_window = DEFAULT_TRACK_SEARCH_WINDOW
        self.track_match_threshold = DEFAULT_TRACK_MATCH_THRESHOLD
        self.tracking_last_pose = None
        self.overlay_position_var = tk.StringVar(value=DEFAULT_OVERLAY_POSITION)
        self.saved_view_scale = None
        self.saved_view_center_x = None
        self.saved_view_center_y = None
        self._view_save_after_id = None
        self._restore_view_after_id = None
        self._view_restore_in_progress = False
        self._window_save_after_id = None
        self._window_save_enabled = False
        self.saved_normal_window_geometry = None
        self.saved_topmost_window_geometry = None
        self.saved_selected_sub_ids = set()
        self._load_config()
        self._load_user_selection()
        self.root.bind("<Escape>", lambda _e: self._disable_topmost_mode())
        self.root.bind("<Configure>", self._on_root_configure)

        self._prepare_points()
        self._setup_style()
        self._build_layout()
        self._load_sub_icons()
        self.map_canvas = self._create_map_canvas(self.map_host)
        self.map_canvas.set_map_opacity(self.map_opacity_var.get() / 100.0)
        self.map_canvas.set_overlay_position(self.overlay_position_var.get())
        self._build_matcher()
        self._build_filters()
        self._apply_saved_user_selection()
        self.root.after(100, self._drain_log_queue)
        self.root.after(100, self._finish_init)

    def _default_config(self):
        return {
            "minimap_left": DEFAULT_MINIMAP_LEFT,
            "minimap_top": DEFAULT_MINIMAP_TOP,
            "minimap_size": DEFAULT_MINIMAP_SIZE,
            "debug_mode": False,
            "icon_size": DEFAULT_ICON_SIZE,
            "map_opacity": int(DEFAULT_MAP_OPACITY * 100),
            "outline_color": "白色",
            "match_scale": "1x",
            "overlay_mode": False,
            "track_follow": True,
            "track_interval_ms": DEFAULT_TRACK_INTERVAL_MS,
            "track_search_window": DEFAULT_TRACK_SEARCH_WINDOW,
            "track_match_threshold": DEFAULT_TRACK_MATCH_THRESHOLD,
            "overlay_position": DEFAULT_OVERLAY_POSITION,
            "view_scale": None,
            "view_center_x": None,
            "view_center_y": None,
            "normal_window_geometry": None,
            "topmost_window_geometry": None,
            "tools_expanded": False,
            "status_expanded": False,
            "detail_expanded": False,
            "log_expanded": False,
        }

    def _load_config(self):
        cfg = self._default_config()
        if CONFIG_PATH.exists():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(loaded)
            except Exception:
                pass
        self.icon_size_var.set(int(cfg["icon_size"]))
        self.map_opacity_var.set(int(cfg["map_opacity"]))
        self.outline_color_name_var.set(str(cfg["outline_color"]))
        self.minimap_size_var.set(int(cfg["minimap_size"]))
        self.minimap_left_var.set(int(cfg["minimap_left"]))
        self.minimap_top_var.set(int(cfg["minimap_top"]))
        match_scale = str(cfg.get("match_scale", "1x"))
        self.match_scale_var.set(match_scale if match_scale in MATCH_SCALE_OPTIONS else "1x")
        self.overlay_mode_var.set(bool(cfg.get("overlay_mode", False)))
        self.track_follow_var.set(bool(cfg.get("track_follow", True)))
        track_interval_ms = int(cfg.get("track_interval_ms", DEFAULT_TRACK_INTERVAL_MS))
        track_interval_ms = max(10, min(5000, track_interval_ms))
        self.track_interval_ms_var.set(track_interval_ms)
        self.track_interval_ms = track_interval_ms
        track_search_window = int(cfg.get("track_search_window", DEFAULT_TRACK_SEARCH_WINDOW))
        track_search_window = max(200, min(4000, track_search_window))
        self.track_search_window_var.set(track_search_window)
        self.track_search_window = track_search_window
        track_match_threshold = float(cfg.get("track_match_threshold", DEFAULT_TRACK_MATCH_THRESHOLD))
        track_match_threshold = max(0.0, min(1.0, track_match_threshold))
        self.track_match_threshold_var.set(track_match_threshold)
        self.track_match_threshold = track_match_threshold
        overlay_position = str(cfg.get("overlay_position", DEFAULT_OVERLAY_POSITION))
        self.overlay_position_var.set(overlay_position if overlay_position in OVERLAY_POSITION_OPTIONS else DEFAULT_OVERLAY_POSITION)
        self.saved_view_scale = cfg.get("view_scale")
        self.saved_view_center_x = cfg.get("view_center_x")
        self.saved_view_center_y = cfg.get("view_center_y")
        self.saved_normal_window_geometry = cfg.get("normal_window_geometry")
        self.saved_topmost_window_geometry = cfg.get("topmost_window_geometry")
        self.debug_mode_var.set(bool(cfg["debug_mode"]))
        self.tools_expanded.set(bool(cfg["tools_expanded"]))
        self.status_expanded.set(bool(cfg["status_expanded"]))
        self.detail_expanded.set(bool(cfg["detail_expanded"]))
        self.log_expanded.set(bool(cfg.get("log_expanded", False)))

    def _save_config(self):
        current_canvas = self._current_map_canvas() if hasattr(self, "_current_map_canvas") else None
        if current_canvas is not None:
            view_state = current_canvas.get_view_state()
            self.saved_view_scale = view_state["scale"]
            self.saved_view_center_x = view_state["center_x"]
            self.saved_view_center_y = view_state["center_y"]
        geometry = self._capture_window_geometry()
        if geometry is not None:
            if self.overlay_mode_var.get():
                self.saved_topmost_window_geometry = geometry
            else:
                self.saved_normal_window_geometry = geometry
        cfg = {
            "minimap_left": int(self.minimap_left_var.get()),
            "minimap_top": int(self.minimap_top_var.get()),
            "minimap_size": int(self.minimap_size_var.get()),
            "debug_mode": bool(self.debug_mode_var.get()),
            "icon_size": int(self.icon_size_var.get()),
            "map_opacity": int(self.map_opacity_var.get()),
            "outline_color": str(self.outline_color_name_var.get()),
            "match_scale": str(self.match_scale_var.get()),
            "overlay_mode": bool(self.overlay_mode_var.get()),
            "track_follow": bool(self.track_follow_var.get()),
            "track_interval_ms": int(self.track_interval_ms_var.get()),
            "track_search_window": int(self.track_search_window_var.get()),
            "track_match_threshold": float(self.track_match_threshold_var.get()),
            "overlay_position": str(self.overlay_position_var.get()),
            "view_scale": self.saved_view_scale,
            "view_center_x": self.saved_view_center_x,
            "view_center_y": self.saved_view_center_y,
            "normal_window_geometry": self.saved_normal_window_geometry,
            "topmost_window_geometry": self.saved_topmost_window_geometry,
            "tools_expanded": bool(self.tools_expanded.get()),
            "status_expanded": bool(self.status_expanded.get()),
            "detail_expanded": bool(self.detail_expanded.get()),
            "log_expanded": bool(self.log_expanded.get()),
        }
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_user_selection(self):
        self.saved_selected_sub_ids = set()
        if not USER_SELECTION_PATH.exists():
            return
        try:
            payload = json.loads(USER_SELECTION_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(payload, dict):
            items = payload.get("selected_sub_ids", [])
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        self.saved_selected_sub_ids = {str(item) for item in items if item}

    def _save_user_selection(self):
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "selected_sub_ids": sorted(self.selected_sub_ids),
        }
        USER_SELECTION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _apply_saved_user_selection(self):
        if not self.saved_selected_sub_ids:
            return
        valid_sub_ids = {sub["sub_id"] for sub in self.subcategory_defs}
        restored = self.saved_selected_sub_ids & valid_sub_ids
        if not restored:
            return
        for sub_id in restored:
            if sub_id in self.sub_vars:
                self.sub_vars[sub_id].set(True)
        self.selected_sub_ids = set(restored)
        for major_id in self.major_vars:
            major_sub_ids = [sub["sub_id"] for sub in self.subcategory_defs if sub["major_id"] == major_id]
            self.major_vars[major_id].set(any(sub_id in self.selected_sub_ids for sub_id in major_sub_ids))

    def _prepare_points(self):
        self.subcategory_defs = []
        for major in self.majors:
            major_id = major["major_id"]
            major_name = major["major_name"]
            color = MAJOR_COLORS.get(major_name, "#e9724c")
            for sub in major["subcategories"]:
                count = len(sub["items"])
                sub_def = {
                    "major_id": major_id,
                    "major_name": major_name,
                    "sub_id": sub["sub_id"],
                    "sub_name": sub["sub_name"],
                    "source": sub["source"],
                    "count": count,
                    "filename": major["_filename"],
                    "image_file": sub.get("image_file", ""),
                }
                self.subcategory_defs.append(sub_def)
                for item in sub["items"]:
                    point = {
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
                    self.points.append(point)

        self.points.sort(key=lambda p: (p["major_id"], p["sub_id"], p["id"]))
        self.subcategory_defs.sort(key=lambda s: (s["major_id"], s["sub_id"]))
        self.selected_sub_ids = set()

    def _build_matcher(self):
        self.matcher = MiniMapMatcher(MAP_IMAGE_PATH)

    def _on_map_view_change(self):
        self.refresh_status_text()
        self._sync_overlay_canvases()
        if self._view_restore_in_progress:
            return
        if self._view_save_after_id is not None:
            self.root.after_cancel(self._view_save_after_id)
        self._view_save_after_id = self.root.after(250, self._persist_view_state)

    def _persist_view_state(self):
        self._view_save_after_id = None
        self._save_config()

    def _restore_saved_view_state(self):
        if self.saved_view_scale is None or self.saved_view_center_x is None or self.saved_view_center_y is None:
            return
        current_canvas = self._current_map_canvas()
        if current_canvas is None or current_canvas.viewport_width <= 1 or current_canvas.viewport_height <= 1:
            self._schedule_restore_saved_view_state()
            return
        self._restore_view_after_id = None
        self._view_restore_in_progress = True
        try:
            current_canvas.restore_view_state(
                float(self.saved_view_scale),
                float(self.saved_view_center_x),
                float(self.saved_view_center_y),
            )
            self._sync_overlay_canvases()
        except Exception:
            self._schedule_restore_saved_view_state()
        finally:
            self._view_restore_in_progress = False

    def _schedule_restore_saved_view_state(self):
        if self._restore_view_after_id is not None:
            self.root.after_cancel(self._restore_view_after_id)
        self._restore_view_after_id = self.root.after(120, self._restore_saved_view_state)

    def _capture_window_geometry(self):
        try:
            target = self.root
            target.update_idletasks()
            return {
                "width": int(target.winfo_width()),
                "height": int(target.winfo_height()),
                "x": int(target.winfo_x()),
                "y": int(target.winfo_y()),
            }
        except Exception:
            return None

    def _apply_saved_window_geometry(self):
        geometry = self.saved_topmost_window_geometry if self.overlay_mode_var.get() else self.saved_normal_window_geometry
        if not isinstance(geometry, dict):
            return
        try:
            width = max(360, int(geometry.get("width", self.root.winfo_width())))
            height = max(260, int(geometry.get("height", self.root.winfo_height())))
            x = int(geometry.get("x", self.root.winfo_x()))
            y = int(geometry.get("y", self.root.winfo_y()))
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _on_root_configure(self, _event=None):
        if not self._window_save_enabled or self.overlay_mode_var.get():
            return
        if self._window_save_after_id is not None:
            self.root.after_cancel(self._window_save_after_id)
        self._window_save_after_id = self.root.after(300, self._persist_window_geometry)

    def _persist_window_geometry(self):
        self._window_save_after_id = None
        self._save_config()

    def _set_sidebar_visible(self, visible: bool):
        panes = {str(pane) for pane in self.shell.panes()}
        sidebar_present = str(self.sidebar) in panes
        map_present = str(self.map_panel) in panes
        if visible and not sidebar_present:
            if map_present:
                self.shell.forget(self.map_panel)
            self.shell.add(self.sidebar, minsize=180, width=520)
            self.shell.add(self.map_panel, minsize=220)
        elif not visible and sidebar_present:
            self.shell.forget(self.sidebar)

    def _apply_topmost_mode(self, enabled: bool):
        self.root.attributes("-topmost", bool(enabled))
        self._set_sidebar_visible(not enabled)
        bg = self.root.cget("bg")
        self.root.configure(bg=bg)
        try:
            self.shell.configure(bg=bg)
        except Exception:
            pass
        try:
            self.root.wm_attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        set_window_clickthrough(self.root, False)
        try:
            self.root.attributes("-alpha", max(0.15, min(1.0, self.map_opacity_var.get() / 100.0)) if enabled else 1.0)
        except tk.TclError:
            pass
        if isinstance(self.map_panel, tk.Frame):
            self.map_panel.configure(bg=bg)
        if isinstance(self.map_host, tk.Frame):
            self.map_host.configure(bg=bg)
        if self.map_canvas is not None:
            self.map_canvas.render_base_map = True
            self.map_canvas.transparent_bg = False
            self.map_canvas.canvas.configure(background="#102844")
            self.map_canvas._invalidate_composited_map()
            self.map_canvas.redraw()

    def _disable_topmost_mode(self):
        if self.overlay_mode_var.get():
            self.overlay_mode_var.set(False)
            self._apply_topmost_mode(False)
            self._save_config()
            self.refresh_status_text()

    def _current_map_canvas(self):
        return self.map_canvas

    def _display_map_canvas(self):
        return self._current_map_canvas()

    def _sync_overlay_canvases(self):
        return

    def _create_map_canvas(self, host, on_view_change=None, **kwargs):
        canvas = MapCanvas(
            host,
            MAP_IMAGE_PATH,
            self.points,
            self.sub_icon_cache,
            self.sub_icon_pil_cache,
            self.show_marker_detail,
            self._on_map_view_change if on_view_change is None else on_view_change,
            **kwargs,
        )
        canvas.pack(fill="both", expand=True)
        return canvas

    def _zoom_map_canvases(self, factor: float):
        current_canvas = self._current_map_canvas()
        if current_canvas is None:
            return
        current_canvas.zoom_by_factor(factor)
        self._sync_overlay_canvases()

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Sidebar.TFrame", background="#efe7d6")
        style.configure("Sidebar.TLabel", background="#efe7d6", foreground="#253240")
        style.configure("SectionHeader.TLabel", background="#efe7d6", foreground="#22313f", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("SidebarTitle.TLabel", background="#efe7d6", foreground="#1f2f3d", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Group.TLabelframe", background="#efe7d6")
        style.configure("Group.TLabelframe.Label", background="#efe7d6", foreground="#22313f", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Info.TLabel", background="#efe7d6", foreground="#51606d")

    def _load_sub_icons(self):
        self.sub_icon_cache = {}
        self.original_sub_icon_images = {}
        for sub in self.subcategory_defs:
            image_file = sub.get("image_file", "")
            image_path = DATA_DIR / image_file if image_file else None
            if image_path and image_path.exists():
                self.original_sub_icon_images[sub["sub_id"]] = Image.open(image_path).convert("RGBA")
        if not self.original_sub_icon_images:
            self.original_sub_icon_images["__fallback__"] = Image.new("RGBA", (18, 18), (255, 200, 0, 255))
        self._rebuild_sub_icon_cache()

    def _render_icon_with_outline(self, base_image: Image.Image, size: int, outline_color: str) -> Image.Image:
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

    def _rebuild_sub_icon_cache(self):
        outline_color = OUTLINE_COLORS.get(self.outline_color_name_var.get(), "#ffffff")
        icon_size = self.icon_size_var.get()
        fallback_tk = None
        fallback_pil = None
        self.sub_icon_cache = {}
        self.sub_icon_pil_cache = {}
        for sub in self.subcategory_defs:
            base = self.original_sub_icon_images.get(sub["sub_id"]) or self.original_sub_icon_images.get("__fallback__")
            if base is None:
                continue
            icon_pil = self._render_icon_with_outline(base, icon_size, outline_color)
            icon_tk = ImageTk.PhotoImage(icon_pil)
            self.sub_icon_cache[sub["sub_id"]] = icon_tk
            self.sub_icon_pil_cache[sub["sub_id"]] = icon_pil
            if fallback_tk is None:
                fallback_tk = icon_tk
                fallback_pil = icon_pil
        if fallback_tk is not None and fallback_pil is not None:
            for sub in self.subcategory_defs:
                self.sub_icon_cache.setdefault(sub["sub_id"], fallback_tk)
                self.sub_icon_pil_cache.setdefault(sub["sub_id"], fallback_pil)

    def _build_layout(self):
        self.shell = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashwidth=6, bg="#cdbf9f", bd=0, relief="flat")
        self.shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(self.shell, style="Sidebar.TFrame", padding=(14, 14, 10, 14), width=520)
        sidebar.pack_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(3, weight=1)
        self.map_panel = tk.Frame(self.shell, bg="#f0f0f0")

        self.sidebar = sidebar
        self.shell.add(sidebar, minsize=180, width=520)
        self.shell.add(self.map_panel, minsize=220)

        self.map_panel.columnconfigure(0, weight=1)
        self.map_panel.rowconfigure(0, weight=1)

        self.map_host = tk.Frame(self.map_panel, bg="#f0f0f0")
        self.map_host.grid(row=0, column=0, sticky="nsew")

        title_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        title_row.grid(row=0, column=0, sticky="we")
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, text="洛克王国世界本地地图", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        zoom_controls = ttk.Frame(title_row, style="Sidebar.TFrame")
        zoom_controls.grid(row=0, column=1, sticky="e")
        tk.Button(
            zoom_controls,
            text="-",
            command=lambda: self._zoom_map_canvases(1 / 1.15),
            relief="flat",
            bg="#e8ddc5",
            activebackground="#d8c9aa",
            bd=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 13, "bold"),
            width=2,
        ).pack(side="left")
        tk.Button(
            zoom_controls,
            text="+",
            command=lambda: self._zoom_map_canvases(1.15),
            relief="flat",
            bg="#e8ddc5",
            activebackground="#d8c9aa",
            bd=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 13, "bold"),
            width=2,
        ).pack(side="left", padx=(4, 0))

        tools_wrap = ttk.Frame(sidebar, style="Sidebar.TFrame")
        tools_wrap.grid(row=1, column=0, sticky="we", pady=(8, 10))
        tools_header = ttk.Frame(tools_wrap, style="Sidebar.TFrame")
        tools_header.pack(fill="x")

        self.tools_toggle_button = tk.Button(
            tools_header,
            text="▸",
            width=2,
            command=self.toggle_tools_panel,
            relief="flat",
            bg="#efe7d6",
            activebackground="#efe7d6",
            bd=0,
            cursor="hand2",
        )
        self.tools_toggle_button.pack(side="left")
        ttk.Label(tools_header, text="工具设置", style="SectionHeader.TLabel").pack(side="left", padx=(2, 0))
        ttk.Checkbutton(
            tools_header,
            text="置于上层",
            variable=self.overlay_mode_var,
            command=self.on_overlay_mode_change,
        ).pack(side="right")

        self.tools_body = ttk.Frame(tools_wrap, style="Sidebar.TFrame")

        visual_wrap = ttk.Frame(self.tools_body, style="Sidebar.TFrame")
        visual_wrap.pack(fill="x")
        visual_wrap.columnconfigure(0, weight=1)

        ttk.Label(visual_wrap, text="标点大小", style="Info.TLabel").grid(row=0, column=0, sticky="w")
        icon_scale = tk.Scale(
            visual_wrap,
            from_=12,
            to=40,
            orient="horizontal",
            variable=self.icon_size_var,
            command=self.on_visual_setting_change,
            showvalue=True,
            resolution=1,
            highlightthickness=0,
            bg="#efe7d6",
            activebackground="#d7caa8",
        )
        icon_scale.grid(row=1, column=0, sticky="we")

        ttk.Label(visual_wrap, text="轮廓颜色", style="Info.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))
        outline_combo = ttk.Combobox(
            visual_wrap,
            textvariable=self.outline_color_name_var,
            values=list(OUTLINE_COLORS.keys()),
            state="readonly",
        )
        outline_combo.grid(row=3, column=0, sticky="we")
        outline_combo.bind("<<ComboboxSelected>>", self.on_visual_setting_change)

        ttk.Label(visual_wrap, text="地图透明度", style="Info.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))
        opacity_scale = tk.Scale(
            visual_wrap,
            from_=20,
            to=100,
            orient="horizontal",
            variable=self.map_opacity_var,
            command=self.on_map_opacity_change,
            showvalue=True,
            resolution=1,
            highlightthickness=0,
            bg="#efe7d6",
            activebackground="#d7caa8",
        )
        opacity_scale.grid(row=5, column=0, sticky="we")

        ttk.Label(visual_wrap, text="匹配倍率", style="Info.TLabel").grid(row=6, column=0, sticky="w", pady=(6, 0))
        match_scale_combo = ttk.Combobox(
            visual_wrap,
            textvariable=self.match_scale_var,
            values=list(MATCH_SCALE_OPTIONS),
            state="readonly",
        )
        match_scale_combo.grid(row=7, column=0, sticky="we")
        match_scale_combo.bind("<<ComboboxSelected>>", self.on_match_scale_change)

        ttk.Label(visual_wrap, text="角落文字位置", style="Info.TLabel").grid(row=8, column=0, sticky="w", pady=(6, 0))
        overlay_position_combo = ttk.Combobox(
            visual_wrap,
            textvariable=self.overlay_position_var,
            values=list(OVERLAY_POSITION_OPTIONS),
            state="readonly",
        )
        overlay_position_combo.grid(row=9, column=0, sticky="we")
        overlay_position_combo.bind("<<ComboboxSelected>>", self.on_overlay_position_change)

        ttk.Checkbutton(
            visual_wrap,
            text="追踪时地图中心跟随",
            variable=self.track_follow_var,
            command=self.on_track_setting_change,
        ).grid(row=10, column=0, sticky="w", pady=(6, 0))

        ttk.Label(visual_wrap, text="追踪间隔 (ms)", style="Info.TLabel").grid(row=11, column=0, sticky="w", pady=(6, 0))
        track_interval_entry = ttk.Entry(visual_wrap, textvariable=self.track_interval_ms_var)
        track_interval_entry.grid(row=12, column=0, sticky="we")
        track_interval_entry.bind("<Return>", self.on_track_setting_change)
        track_interval_entry.bind("<FocusOut>", self.on_track_setting_change)

        ttk.Label(visual_wrap, text="局部搜索范围", style="Info.TLabel").grid(row=13, column=0, sticky="w", pady=(6, 0))
        track_window_entry = ttk.Entry(visual_wrap, textvariable=self.track_search_window_var)
        track_window_entry.grid(row=14, column=0, sticky="we")
        track_window_entry.bind("<Return>", self.on_track_setting_change)
        track_window_entry.bind("<FocusOut>", self.on_track_setting_change)

        ttk.Label(visual_wrap, text="局部匹配阈值", style="Info.TLabel").grid(row=15, column=0, sticky="w", pady=(6, 0))
        track_threshold_entry = ttk.Entry(visual_wrap, textvariable=self.track_match_threshold_var)
        track_threshold_entry.grid(row=16, column=0, sticky="we")
        track_threshold_entry.bind("<Return>", self.on_track_setting_change)
        track_threshold_entry.bind("<FocusOut>", self.on_track_setting_change)

        ttk.Label(visual_wrap, text="校准框大小", style="Info.TLabel").grid(row=17, column=0, sticky="w", pady=(6, 0))
        minimap_size_entry = ttk.Entry(visual_wrap, textvariable=self.minimap_size_var)
        minimap_size_entry.grid(row=18, column=0, sticky="we")
        minimap_size_entry.bind("<Return>", self.on_minimap_size_change)
        minimap_size_entry.bind("<FocusOut>", self.on_minimap_size_change)

        ttk.Label(visual_wrap, text="校准框左上角 X", style="Info.TLabel").grid(row=19, column=0, sticky="w", pady=(6, 0))
        minimap_left_entry = ttk.Entry(visual_wrap, textvariable=self.minimap_left_var)
        minimap_left_entry.grid(row=20, column=0, sticky="we")
        minimap_left_entry.bind("<Return>", self.on_minimap_position_change)
        minimap_left_entry.bind("<FocusOut>", self.on_minimap_position_change)

        ttk.Label(visual_wrap, text="校准框左上角 Y", style="Info.TLabel").grid(row=21, column=0, sticky="w", pady=(6, 0))
        minimap_top_entry = ttk.Entry(visual_wrap, textvariable=self.minimap_top_var)
        minimap_top_entry.grid(row=22, column=0, sticky="we")
        minimap_top_entry.bind("<Return>", self.on_minimap_position_change)
        minimap_top_entry.bind("<FocusOut>", self.on_minimap_position_change)

        ttk.Checkbutton(
            visual_wrap,
            text="调试模式",
            variable=self.debug_mode_var,
            command=self.on_debug_mode_change,
        ).grid(row=23, column=0, sticky="w", pady=(8, 0))

        self.log_toggle_button = ttk.Button(visual_wrap, text="打开日志", command=self.toggle_log_panel)
        self.log_toggle_button.grid(row=24, column=0, sticky="we", pady=(8, 0))

        button_bar = ttk.Frame(sidebar, style="Sidebar.TFrame")
        button_bar.grid(row=2, column=0, sticky="we", pady=(4, 10))
        top_button_row = ttk.Frame(button_bar, style="Sidebar.TFrame")
        top_button_row.pack(fill="x")
        ttk.Button(top_button_row, text="适应窗口", command=self.fit_map).pack(side="left")
        ttk.Button(top_button_row, text="校准小地图", command=self.open_minimap_selector).pack(side="left", padx=6)
        ttk.Button(top_button_row, text="截取并定位", command=self.capture_and_locate_player).pack(side="left")
        self.track_button = ttk.Button(top_button_row, text="实时追踪", command=self.toggle_tracking)
        self.track_button.pack(side="left", padx=(6, 0))

        marker_group = ttk.LabelFrame(sidebar, text="标点筛选", style="Group.TLabelframe", padding=(8, 8))
        marker_group.grid(row=3, column=0, sticky="nsew")
        marker_group.columnconfigure(0, weight=1)
        marker_group.rowconfigure(2, weight=1)

        filter_controls = ttk.Frame(marker_group, style="Sidebar.TFrame")
        filter_controls.grid(row=0, column=0, sticky="we")
        ttk.Button(filter_controls, text="全选", command=self.select_all_subcategories).pack(side="left")
        ttk.Button(filter_controls, text="清空", command=self.clear_all_subcategories).pack(side="left", padx=6)

        search_wrap = ttk.Frame(marker_group, style="Sidebar.TFrame")
        search_wrap.grid(row=1, column=0, sticky="we", pady=(8, 8))
        ttk.Label(search_wrap, text="搜索子类或点位名称", style="Info.TLabel").pack(anchor="w", pady=(0, 4))
        search_entry = ttk.Entry(search_wrap, textvariable=self.search_var)
        search_entry.pack(fill="x")
        search_entry.bind("<KeyRelease>", lambda _e: self.apply_filters())

        self.filter_scroller = ScrollableFrame(marker_group)
        self.filter_scroller.grid(row=2, column=0, sticky="nsew")

        bottom_info = ttk.Frame(sidebar, style="Sidebar.TFrame")
        bottom_info.grid(row=4, column=0, sticky="we", pady=(10, 6))
        bottom_info.columnconfigure(0, weight=1)
        bottom_info.columnconfigure(1, weight=1)

        self.status_panel = ttk.Frame(bottom_info, style="Sidebar.TFrame")
        self.status_panel.grid(row=0, column=0, sticky="sw")
        self.status_toggle_button = tk.Button(
            self.status_panel,
            text="▸ 状态",
            command=self.toggle_status_panel,
            relief="flat",
            bg="#efe7d6",
            activebackground="#efe7d6",
            bd=0,
            cursor="hand2",
            anchor="w",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.status_toggle_button.pack(anchor="w")
        self.status_label = ttk.Label(self.status_panel, textvariable=self.status_var, style="Info.TLabel", wraplength=220, justify="left")

        self.detail_panel = ttk.Frame(bottom_info, style="Sidebar.TFrame")
        self.detail_panel.grid(row=0, column=1, sticky="se")
        self.detail_toggle_button = tk.Button(
            self.detail_panel,
            text="▸ 点位详情",
            command=self.toggle_detail_panel,
            relief="flat",
            bg="#efe7d6",
            activebackground="#efe7d6",
            bd=0,
            cursor="hand2",
            anchor="e",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.detail_toggle_button.pack(anchor="e")
        self.detail_label = ttk.Label(self.detail_panel, textvariable=self.detail_var, style="Info.TLabel", wraplength=220, justify="left")

    def _build_filters(self):
        for widget in self.filter_scroller.interior.winfo_children():
            widget.destroy()
        self.major_vars.clear()
        self.sub_vars.clear()
        self.sub_rows.clear()
        self.major_frames.clear()
        self.major_bodies.clear()
        self.major_expanded.clear()
        self.sub_icon_labels.clear()

        major_to_subs = {}
        for sub in self.subcategory_defs:
            major_to_subs.setdefault((sub["major_id"], sub["major_name"]), []).append(sub)

        for (major_id, major_name), subs in sorted(major_to_subs.items(), key=lambda item: item[0][0]):
            total = sum(sub["count"] for sub in subs)
            frame = ttk.LabelFrame(self.filter_scroller.interior, text="", style="Group.TLabelframe", padding=(8, 8))
            frame.pack(fill="x", pady=(0, 10))
            self.major_frames[major_id] = frame

            header = ttk.Frame(frame, style="Sidebar.TFrame")
            header.pack(fill="x", pady=(0, 6))

            self.major_expanded[major_id] = tk.BooleanVar(value=True)
            fold_button = tk.Button(
                header,
                text=f"{'▾'} {major_name}（{total}）",
                command=lambda m_id=major_id: self.toggle_major_fold(m_id),
                relief="flat",
                bg="#efe7d6",
                activebackground="#efe7d6",
                bd=0,
                cursor="hand2",
                anchor="w",
                font=("Microsoft YaHei UI", 11, "bold"),
            )
            fold_button.pack(side="left", fill="x", expand=True)

            var = tk.BooleanVar(value=False)
            self.major_vars[major_id] = var
            ttk.Checkbutton(
                header,
                text="",
                variable=var,
                command=lambda m_id=major_id: self.toggle_major(m_id),
            ).pack(side="left")

            body = ttk.Frame(frame, style="Sidebar.TFrame")
            body.pack(fill="x")
            self.major_bodies[major_id] = {"frame": body, "button": fold_button}

            for sub in subs:
                row = ttk.Frame(body, style="Sidebar.TFrame")
                row.pack(fill="x", pady=2)
                self.sub_rows[sub["sub_id"]] = row
                var = tk.BooleanVar(value=False)
                self.sub_vars[sub["sub_id"]] = var

                icon = self.sub_icon_cache.get(sub["sub_id"])
                icon_label = tk.Label(row, image=icon, bg="#efe7d6")
                icon_label.image = icon
                icon_label.pack(side="left", padx=(0, 6))
                self.sub_icon_labels[sub["sub_id"]] = icon_label

                ttk.Checkbutton(
                    row,
                    text=f"{sub['sub_name']} ({sub['count']})",
                    variable=var,
                    command=self.on_subcategory_toggle,
                ).pack(side="left", anchor="w")

    def toggle_major_fold(self, major_id: str):
        state = self.major_expanded[major_id].get()
        body = self.major_bodies[major_id]["frame"]
        button = self.major_bodies[major_id]["button"]
        if state:
            body.pack_forget()
            major_name = next(sub["major_name"] for sub in self.subcategory_defs if sub["major_id"] == major_id)
            total = sum(sub["count"] for sub in self.subcategory_defs if sub["major_id"] == major_id)
            button.config(text=f"▸ {major_name}（{total}）")
            self.major_expanded[major_id].set(False)
        else:
            body.pack(fill="x")
            major_name = next(sub["major_name"] for sub in self.subcategory_defs if sub["major_id"] == major_id)
            total = sum(sub["count"] for sub in self.subcategory_defs if sub["major_id"] == major_id)
            button.config(text=f"▾ {major_name}（{total}）")
            self.major_expanded[major_id].set(True)

    def _finish_init(self):
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.fit_to_view()
        self._sync_overlay_canvases()
        self.apply_filters()
        if self.tools_expanded.get():
            self.tools_body.pack(fill="x", pady=(6, 0))
            self.tools_toggle_button.config(text="▾")
        if self.status_expanded.get():
            self.status_label.pack(anchor="w", pady=(4, 0))
            self.status_toggle_button.config(text="▾ 状态")
        if self.detail_expanded.get():
            self.detail_label.pack(anchor="e", pady=(4, 0))
            self.detail_toggle_button.config(text="▾ 点位详情")
        if self.log_expanded.get():
            self._open_log_window()
        if self.saved_normal_window_geometry or self.saved_topmost_window_geometry:
            self._apply_saved_window_geometry()
        else:
            self._center_window()
        self._apply_topmost_mode(self.overlay_mode_var.get())
        self._window_save_enabled = True
        self.root.after(0, self._restore_saved_view_state)

    def _append_log(self, message: str):
        if not self.log_expanded.get() or self.log_window is None or self.log_text is None:
            return
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def _clear_log_queue(self):
        while True:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break

    def _on_log_window_close(self):
        self.log_expanded.set(False)
        self.log_toggle_button.config(text="打开日志")
        self._clear_log_queue()
        if self.log_window is not None:
            self.log_window.destroy()
        self.log_window = None
        self.log_text = None
        self._save_config()

    def _open_log_window(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.deiconify()
            self.log_window.lift()
            self.log_toggle_button.config(text="关闭日志")
            self.log_expanded.set(True)
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("运行日志")
        self.log_window.geometry(f"{self.log_panel_width}x640")
        self.log_window.minsize(260, 320)
        self.log_window.protocol("WM_DELETE_WINDOW", self._on_log_window_close)
        self.log_window.rowconfigure(1, weight=1)
        self.log_window.columnconfigure(0, weight=1)

        ttk.Label(self.log_window, text="运行日志", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 6))
        log_wrap = ttk.Frame(self.log_window)
        log_wrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        log_wrap.rowconfigure(0, weight=1)
        log_wrap.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_wrap, wrap="word", state="disabled", bg="#f7f3ea", fg="#3b4d61", relief="flat")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_toggle_button.config(text="关闭日志")
        self.log_expanded.set(True)

    def _drain_log_queue(self):
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines and self.log_window is not None and self.log_text is not None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        elif lines:
            self._clear_log_queue()
        self.root.after(100, self._drain_log_queue)

    def toggle_log_panel(self):
        if self.log_expanded.get():
            self._on_log_window_close()
        else:
            self._open_log_window()
            self._save_config()

    def toggle_tools_panel(self):
        if self.tools_expanded.get():
            self.tools_body.pack_forget()
            self.tools_toggle_button.config(text="▸")
            self.tools_expanded.set(False)
        else:
            self.tools_body.pack(fill="x", pady=(6, 0))
            self.tools_toggle_button.config(text="▾")
            self.tools_expanded.set(True)
        self._save_config()

    def toggle_status_panel(self):
        if self.status_expanded.get():
            self.status_label.pack_forget()
            self.status_toggle_button.config(text="▸ 状态")
            self.status_expanded.set(False)
        else:
            self.status_label.pack(anchor="w", pady=(4, 0))
            self.status_toggle_button.config(text="▾ 状态")
            self.status_expanded.set(True)
        self._save_config()

    def toggle_detail_panel(self):
        if self.detail_expanded.get():
            self.detail_label.pack_forget()
            self.detail_toggle_button.config(text="▸ 点位详情")
            self.detail_expanded.set(False)
        else:
            self.detail_label.pack(anchor="e", pady=(4, 0))
            self.detail_toggle_button.config(text="▾ 点位详情")
            self.detail_expanded.set(True)
        self._save_config()

    def _center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def toggle_major(self, major_id: str):
        selected = self.major_vars[major_id].get()
        for sub in self.subcategory_defs:
            if sub["major_id"] != major_id:
                continue
            self.sub_vars[sub["sub_id"]].set(selected)
            if selected:
                self.selected_sub_ids.add(sub["sub_id"])
            else:
                self.selected_sub_ids.discard(sub["sub_id"])
        self._save_user_selection()
        self.apply_filters()

    def on_subcategory_toggle(self):
        self.selected_sub_ids = {sub_id for sub_id, var in self.sub_vars.items() if var.get()}
        for major_id in self.major_vars:
            major_sub_ids = [sub["sub_id"] for sub in self.subcategory_defs if sub["major_id"] == major_id]
            self.major_vars[major_id].set(any(self.sub_vars[sub_id].get() for sub_id in major_sub_ids))
        self._save_user_selection()
        self.apply_filters()

    def select_all_subcategories(self):
        for major_id in self.major_vars:
            self.major_vars[major_id].set(True)
        for sub_id in self.sub_vars:
            self.sub_vars[sub_id].set(True)
        self.selected_sub_ids = set(self.sub_vars.keys())
        self._save_user_selection()
        self.apply_filters()

    def clear_all_subcategories(self):
        for major_id in self.major_vars:
            self.major_vars[major_id].set(False)
        for sub_id in self.sub_vars:
            self.sub_vars[sub_id].set(False)
        self.selected_sub_ids.clear()
        self._save_user_selection()
        self.apply_filters()

    def fit_map(self):
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.fit_to_view()
            self._sync_overlay_canvases()

    def toggle_tracking(self):
        if self.tracking_active:
            self.stop_tracking("已停止实时追踪")
        else:
            self.start_tracking()

    def start_tracking(self):
        if self.tracking_active:
            return
        bbox = self._get_minimap_bbox()
        full_search_scale_label = self.match_scale_var.get()
        selector_was_visible = bool(
            self.minimap_selector is not None and self.minimap_selector.win.winfo_viewable()
        )
        self.on_track_setting_change()
        self.tracking_active = True
        self.tracking_restore_selector = selector_was_visible
        self.tracking_stop_event = threading.Event()
        stop_event = self.tracking_stop_event
        self.tracking_last_pose = None
        self.track_button.config(text="停止追踪")
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.set_player_candidates([])
        self.minimap_match_status = "实时追踪中..."
        self.refresh_status_text()
        self._append_log(
            f"开始实时追踪: 截图区域={bbox}, 全图倍率={full_search_scale_label}, 局部倍率=1x, "
            f"局部范围={self.track_search_window}px, 阈值={self.track_match_threshold:.3f}"
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
        self.track_button.config(text="实时追踪")
        if self.tracking_restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        self.tracking_restore_selector = False
        if message:
            self.minimap_match_status = message
            self.refresh_status_text()

    def _get_minimap_bbox(self):
        x = int(self.minimap_left_var.get())
        y = int(self.minimap_top_var.get())
        size = int(self.minimap_size_var.get())
        return x, y, x + size, y + size

    def open_minimap_selector(self):
        if self.minimap_selector is None:
            self.minimap_selector = MiniMapSelector(self.root, self._on_selector_changed)
        self.minimap_selector.set_size(self.minimap_size_var.get())
        self.minimap_selector.set_position(self.minimap_left_var.get(), self.minimap_top_var.get())
        self.minimap_selector.show()
        self.minimap_match_status = "已打开校准框"
        self._append_log(f"打开校准框: 左上角=({self.minimap_left_var.get()}, {self.minimap_top_var.get()}), 大小={self.minimap_size_var.get()}")
        self.refresh_status_text()

    def _capture_minimap(self, bbox, log=True):
        capture = ImageGrab.grab(bbox=bbox).convert("RGB")
        if log:
            self._append_log(f"截图完成: 尺寸={capture.size}")
        return capture

    def _run_locate_attempt(self, bbox, match_scale_label: str, selector_was_visible: bool, track_mode: bool):
        attempt_started = time.perf_counter()
        try:
            capture = self._capture_minimap(bbox)
        except Exception as exc:
            self._append_log(f"截图失败: {exc}")
            if track_mode:
                self.root.after(0, lambda: self._on_tracking_frame_failed(f"实时追踪截图失败: {exc}"))
            else:
                self.root.after(0, lambda: self._on_locate_failed(f'截图失败: {exc}', selector_was_visible))
            return None

        stamp = time.strftime("%Y%m%d_%H%M%S")
        if self.debug_mode_var.get() and not track_mode:
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            capture_path = TEMP_DIR / f"minimap_capture_{stamp}.png"
            capture.save(capture_path)
            self._append_log(f"调试截图已保存: {capture_path}")

        self._append_log("开始全图匹配...")
        result = self.matcher.match(capture, scale_label=match_scale_label, progress_cb=self._append_log)
        if not result:
            self._append_log("匹配失败: 未找到结果")
            if track_mode:
                self.root.after(0, lambda: self._on_tracking_frame_failed("实时追踪未找到匹配位置"))
            else:
                self.root.after(0, lambda: self._on_locate_failed("未找到匹配位置", selector_was_visible))
            return None

        self._append_log(
            f"匹配完成: scope={result.get('search_scope', 'full')}, x={result['x_map']}, y={result['y_map']}, score={result['score']:.3f}, "
            f"source={result['source_capture_size']}, normalized={result['normalized_capture_size']}, "
            f"scale={result['match_scale_label']}, template={result['template_size']}, scaled_map={result['scaled_map_size']}, "
            f"rgb={result['rgb_elapsed_ms']:.2f}ms total={result['total_elapsed_ms']:.2f}ms"
        )
        if self.debug_mode_var.get() and not track_mode:
            debug_path = TEMP_DIR / f"minimap_match_{stamp}.json"
            debug_payload = {
                "capture_box": bbox,
                "match_result": result,
            }
            debug_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._append_log(f"匹配结果已保存: {debug_path}")

        callback_queued_at = time.perf_counter()
        if track_mode:
            self.root.after(0, lambda: self._on_locate_success(result, False, True, attempt_started, callback_queued_at))
        else:
            self.root.after(0, lambda: self._on_locate_success(result, selector_was_visible, False, attempt_started, callback_queued_at))
        return result

    def _run_tracking_cycle(self, bbox, full_search_scale_label: str):
        attempt_started = time.perf_counter()
        verbose_tracking_logs = bool(self.debug_mode_var.get())
        try:
            capture = self._capture_minimap(bbox, log=verbose_tracking_logs)
        except Exception as exc:
            self._append_log(f"截图失败: {exc}")
            self.root.after(0, lambda: self._on_tracking_frame_failed(f"实时追踪截图失败: {exc}"))
            return None

        result = None
        used_local = False
        if self.tracking_last_pose is not None:
            used_local = True
            if verbose_tracking_logs:
                self._append_log(
                    f"开始局部搜索: 中心=({self.tracking_last_pose[0]}, {self.tracking_last_pose[1]}), "
                    f"范围={self.track_search_window}px, 阈值={self.track_match_threshold:.3f}"
                )
            result = self.matcher.match(
                capture,
                scale_label="1x",
                local_center_map=self.tracking_last_pose,
                local_window_map=self.track_search_window,
                progress_cb=self._append_log if verbose_tracking_logs else None,
            )
            if result is None or result["score"] > self.track_match_threshold:
                local_score = "None" if result is None else f"{result['score']:.3f}"
                self._append_log(
                    f"局部搜索回退全图: score={local_score}, threshold={self.track_match_threshold:.3f}"
                )
                self.root.after(0, lambda: self._current_map_canvas().set_overlay_alert("失锁", "#ff4b4b") if self._current_map_canvas() is not None else None)
                result = None

        if result is None:
            if verbose_tracking_logs:
                self._append_log(f"开始全图搜索: 倍率={full_search_scale_label}")
            result = self.matcher.match(
                capture,
                scale_label=full_search_scale_label,
                progress_cb=self._append_log if verbose_tracking_logs else None,
            )
            if not result:
                self._append_log("全图搜索失败: 未找到结果")
                self.root.after(0, lambda: self._on_tracking_frame_failed("实时追踪未找到匹配位置"))
                return None

        self._append_log(
            f"追踪匹配完成: scope={result['search_scope']}, x={result['x_map']}, y={result['y_map']}, "
            f"score={result['score']:.3f}, "
            f"rgb={result['rgb_elapsed_ms']:.2f}ms, "
            f"total={result['total_elapsed_ms']:.2f}ms"
        )
        self.tracking_last_pose = (result["x_map"], result["y_map"])
        callback_queued_at = time.perf_counter()
        self.root.after(0, lambda: self._on_locate_success(result, False, True, attempt_started, callback_queued_at))
        return result

    def capture_and_locate_player(self):
        if self.tracking_active:
            self.minimap_match_status = "请先停止实时追踪"
            self.refresh_status_text()
            return
        bbox = self._get_minimap_bbox()
        match_scale_label = self.match_scale_var.get()
        selector_was_visible = bool(
            self.minimap_selector is not None and self.minimap_selector.win.winfo_viewable()
        )
        self._append_log(f"开始定位: 截图区域={bbox}")
        self.minimap_match_status = "定位中..."
        self.refresh_status_text()

        def worker():
            if selector_was_visible:
                self.root.after(0, lambda: self.minimap_selector.hide())
            self._run_locate_attempt(
                bbox=bbox,
                match_scale_label=match_scale_label,
                selector_was_visible=selector_was_visible,
                track_mode=False,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_locate_failed(self, message: str, restore_selector=False):
        if restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.apply_location_result(None, candidates=[], follow_center=False)
            current_canvas.set_overlay_match_error(None)
        self.minimap_match_status = message
        self.refresh_status_text()

    def _on_tracking_frame_failed(self, message: str):
        if self.tracking_active:
            self.tracking_last_pose = None
            current_canvas = self._current_map_canvas()
            if current_canvas is not None:
                current_canvas.set_overlay_alert("失锁", "#ff4b4b")
            self.minimap_match_status = message
            self.refresh_status_text()

    def _on_locate_success(self, result: dict, restore_selector=False, track_mode=False, attempt_started=None, callback_queued_at=None):
        if track_mode and not self.tracking_active:
            return
        ui_queue_wait_ms = None
        if callback_queued_at is not None:
            ui_queue_wait_ms = round((time.perf_counter() - callback_queued_at) * 1000.0, 2)
            result["ui_queue_wait_ms"] = ui_queue_wait_ms
            self._append_log(f"UI排队等待: {ui_queue_wait_ms:.2f}ms")
        ui_started = time.perf_counter()
        if restore_selector and self.minimap_selector is not None:
            self.minimap_selector.show()
        current_canvas = self._current_map_canvas()
        if track_mode:
            if current_canvas is not None:
                current_canvas.apply_location_result(
                    (result["x_map"], result["y_map"]),
                    candidates=[],
                    follow_center=bool(self.track_follow_var.get()),
                )
                self._sync_overlay_canvases()
            if result.get("search_scope") == "local":
                if current_canvas is not None:
                    current_canvas.set_overlay_alert("", "#ff4b4b")
        else:
            if current_canvas is not None:
                current_canvas.apply_location_result(
                    (result["x_map"], result["y_map"]),
                    candidates=result.get("top_matches", []),
                    follow_center=False,
                )
                current_canvas.center_on_map_point(result["x_map"], result["y_map"])
                current_canvas.set_overlay_alert("", "#ff4b4b")
                self._sync_overlay_canvases()
        if current_canvas is not None:
            current_canvas.set_overlay_match_error(result.get("score"))
        end_to_end_ms = None
        ui_update_elapsed_ms = round((time.perf_counter() - ui_started) * 1000.0, 2)
        result["ui_update_elapsed_ms"] = ui_update_elapsed_ms
        self._append_log(f"地图更新耗时: {ui_update_elapsed_ms:.2f}ms")
        if attempt_started is not None:
            end_to_end_ms = round((time.perf_counter() - attempt_started) * 1000.0, 2)
            result["end_to_end_elapsed_ms"] = end_to_end_ms
            self._append_log(
                f"端到端耗时: {end_to_end_ms:.2f}ms "
                f"(从准备截图到地图完成更新)"
            )
        top_scores = " | ".join(
            f"{row['rank']}:{row['score']:.3f}"
            for row in result.get("top_matches", [])[:5]
        )
        if track_mode:
            self.minimap_match_status = (
                f"实时追踪: ({result['x_map']}, {result['y_map']}) "
                f"方式 {result.get('search_scope', 'full')} "
                f"差异值 {result['score']:.3f} "
                f"倍率 {result['match_scale_label']} "
                f"RGB {result['rgb_elapsed_ms']:.1f}ms "
                f"排队 {result.get('ui_queue_wait_ms', 0.0):.1f}ms "
                f"地图 {result['ui_update_elapsed_ms']:.1f}ms "
                f"搜索 {result['total_elapsed_ms']:.1f}ms "
                f"端到端 {end_to_end_ms:.1f}ms" if end_to_end_ms is not None else
                f"实时追踪: ({result['x_map']}, {result['y_map']}) "
                f"方式 {result.get('search_scope', 'full')} "
                f"差异值 {result['score']:.3f} "
                f"倍率 {result['match_scale_label']} "
                f"RGB {result['rgb_elapsed_ms']:.1f}ms "
                f"排队 {result.get('ui_queue_wait_ms', 0.0):.1f}ms "
                f"地图 {result['ui_update_elapsed_ms']:.1f}ms "
                f"搜索 {result['total_elapsed_ms']:.1f}ms"
            )
        else:
            tail = (
                f"RGB {result['rgb_elapsed_ms']:.1f}ms "
                f"排队 {result.get('ui_queue_wait_ms', 0.0):.1f}ms "
                f"地图 {result['ui_update_elapsed_ms']:.1f}ms "
                f"搜索 {result['total_elapsed_ms']:.1f}ms "
                f"端到端 {end_to_end_ms:.1f}ms "
            ) if end_to_end_ms is not None else (
                f"RGB {result['rgb_elapsed_ms']:.1f}ms "
                f"排队 {result.get('ui_queue_wait_ms', 0.0):.1f}ms "
                f"地图 {result['ui_update_elapsed_ms']:.1f}ms "
                f"搜索 {result['total_elapsed_ms']:.1f}ms "
            )
            self.minimap_match_status = (
                f"定位完成: ({result['x_map']}, {result['y_map']}) "
                f"方式 {result.get('search_scope', 'full')} "
                f"差异值 {result['score']:.3f} "
                f"原始截图 {result['source_capture_size']} "
                f"归一化 {result['normalized_capture_size']} "
                f"倍率 {result['match_scale_label']} "
                f"截图 {result['capture_size']} "
                f"模板 {result['template_size']} "
                f"缩放地图 {result['scaled_map_size']} "
                f"匹配左上角 ({result['match_left']}, {result['match_top']}) "
                f"{tail}"
                f"[Top {len(result.get('top_matches', []))}: {top_scores}]"
            )
        self.refresh_status_text()

    def on_visual_setting_change(self, _event=None):
        self._rebuild_sub_icon_cache()
        for sub_id, label in self.sub_icon_labels.items():
            icon = self.sub_icon_cache.get(sub_id)
            if icon is not None:
                label.configure(image=icon)
                label.image = icon
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.set_icon_cache(self.sub_icon_cache, self.sub_icon_pil_cache)
        self._save_config()
        self.refresh_status_text()

    def on_map_opacity_change(self, _event=None):
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.set_map_opacity(self.map_opacity_var.get() / 100.0)
        if self.overlay_mode_var.get():
            self._apply_topmost_mode(True)
        self._save_config()
        self.refresh_status_text()

    def on_debug_mode_change(self):
        self._save_config()
        self.refresh_status_text()

    def on_match_scale_change(self, _event=None):
        if self.match_scale_var.get() not in MATCH_SCALE_OPTIONS:
            self.match_scale_var.set("1x")
        self.minimap_match_status = f"匹配倍率: {self.match_scale_var.get()}"
        self._save_config()
        self.refresh_status_text()

    def on_overlay_position_change(self, _event=None):
        if self.overlay_position_var.get() not in OVERLAY_POSITION_OPTIONS:
            self.overlay_position_var.set(DEFAULT_OVERLAY_POSITION)
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.set_overlay_position(self.overlay_position_var.get())
        self._save_config()
        self.refresh_status_text()

    def on_overlay_mode_change(self):
        self._apply_topmost_mode(self.overlay_mode_var.get())
        self.minimap_match_status = f"置于上层: {'开' if self.overlay_mode_var.get() else '关'}"
        self._save_config()
        self.refresh_status_text()

    def on_track_setting_change(self, _event=None):
        try:
            value = int(self.track_interval_ms_var.get())
        except Exception:
            value = self.track_interval_ms or DEFAULT_TRACK_INTERVAL_MS
        value = max(10, min(5000, value))
        self.track_interval_ms_var.set(value)
        self.track_interval_ms = value
        try:
            search_window = int(self.track_search_window_var.get())
        except Exception:
            search_window = self.track_search_window or DEFAULT_TRACK_SEARCH_WINDOW
        search_window = max(200, min(4000, search_window))
        self.track_search_window_var.set(search_window)
        self.track_search_window = search_window
        try:
            match_threshold = float(self.track_match_threshold_var.get())
        except Exception:
            match_threshold = self.track_match_threshold or DEFAULT_TRACK_MATCH_THRESHOLD
        match_threshold = max(0.0, min(1.0, match_threshold))
        self.track_match_threshold_var.set(match_threshold)
        self.track_match_threshold = match_threshold
        self.minimap_match_status = (
            f"追踪设置: 跟随={'开' if self.track_follow_var.get() else '关'} "
            f"间隔={self.track_interval_ms}ms "
            f"范围={self.track_search_window}px "
            f"阈值={self.track_match_threshold:.3f}"
        )
        self._save_config()
        self.refresh_status_text()

    def on_minimap_size_change(self, _event=None):
        value = self.minimap_size_var.get()
        value = max(80, min(360, int(value)))
        self.minimap_size_var.set(value)
        if self.minimap_selector is not None:
            self.minimap_selector.set_size(value)
            self.minimap_match_status = f"校准框大小: {value}"
        self._save_config()
        self.refresh_status_text()

    def on_minimap_position_change(self, _event=None):
        x = int(self.minimap_left_var.get())
        y = int(self.minimap_top_var.get())
        self.minimap_left_var.set(x)
        self.minimap_top_var.set(y)
        if self.minimap_selector is not None:
            self.minimap_selector.set_position(x, y)
            self.minimap_match_status = f"校准框左上角: ({x}, {y})"
        self._save_config()
        self.refresh_status_text()

    def _on_selector_changed(self, x: int, y: int, size: int):
        self.minimap_left_var.set(int(x))
        self.minimap_top_var.set(int(y))
        self.minimap_size_var.set(int(size))
        self._save_config()
        if self.debug_mode_var.get():
            self.refresh_status_text()

    def apply_filters(self):
        needle = self.search_var.get().strip().lower()
        visible_sub_rows = 0
        for sub in self.subcategory_defs:
            row = self.sub_rows[sub["sub_id"]]
            matches_name = not needle or needle in sub["sub_name"].lower() or needle in sub["major_name"].lower()
            matches_item = False
            if needle:
                matches_item = any(
                    needle in point["name"].lower()
                    for point in self.points
                    if point["sub_id"] == sub["sub_id"]
                )
            if matches_name or matches_item or not needle:
                if not row.winfo_manager():
                    row.pack(fill="x", pady=2)
                visible_sub_rows += 1
            else:
                row.pack_forget()

        visible_count = sum(
            1
            for point in self.points
            if point["sub_id"] in self.selected_sub_ids
            and (
                not needle
                or needle in point["name"].lower()
                or needle in point["sub_name"].lower()
                or needle in point["major_name"].lower()
            )
        )
        self.current_visible_marker_count = visible_count
        current_canvas = self._current_map_canvas()
        if current_canvas is not None:
            current_canvas.set_filters(self.selected_sub_ids, needle)
        self.refresh_status_text()

    def refresh_status_text(self):
        debug_lines = ""
        if self.debug_mode_var.get() and self.minimap_selector is not None:
            tl_x, tl_y = self.minimap_selector.top_left()
            bl_x, bl_y = self.minimap_selector.bottom_left()
            tr_x, tr_y = self.minimap_selector.top_right()
            br_x, br_y = self.minimap_selector.bottom_right()
            debug_lines = (
                f"\n调试-校准框左上角: ({tl_x}, {tl_y})"
                f"\n调试-校准框左下角: ({bl_x}, {bl_y})"
                f"\n调试-校准框右上角: ({tr_x}, {tr_y})"
                f"\n调试-校准框右下角: ({br_x}, {br_y})"
            )
        self.status_var.set(
            f"数据目录: {DATA_DIR}\n"
            f"已加载大类: {len(self.majors)}\n"
            f"已加载点位: {len(self.points)}\n"
            f"当前选中子类: {len(self.selected_sub_ids)} / {len(self.sub_vars)}\n"
            f"当前显示点位: {self.current_visible_marker_count} / {len(self.points)}\n"
            f"当前底图分辨率: {self._display_map_canvas().current_lod_size if self._display_map_canvas() is not None else 0}px\n"
            f"图标大小: {self.icon_size_var.get()}   透明度: {self.map_opacity_var.get()}%   匹配倍率: {self.match_scale_var.get()}\n"
            f"追踪跟随: {'开' if self.track_follow_var.get() else '关'}   追踪间隔: {self.track_interval_ms}ms   范围: {self.track_search_window}px   阈值: {self.track_match_threshold:.3f}\n"
            f"小地图定位: {self.minimap_match_status}"
            f"{debug_lines}"
        )
        current_canvas = self._current_map_canvas()
        display_canvas = self._display_map_canvas()
        if current_canvas is not None and display_canvas is not None:
            current_canvas.set_overlay_text(
                f"缩放: {display_canvas.scale * 100:.0f}%   底图: {display_canvas.current_lod_size}px"
            )

    def show_marker_detail(self, point: dict):
        self.detail_var.set(
            f"名称: {point['name']}\n"
            f"分类: {point['major_name']} / {point['sub_name']}\n"
            f"编号: {point['id']}\n"
            f"像素坐标: ({point['x_map']}, {point['y_map']})\n"
            f"来源: {point['source']}"
        )


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


def run(smoke_test: bool = False):
    validate_inputs()
    root = tk.Tk()
    DesktopMapApp(root)
    if smoke_test:
        root.after(1200, root.destroy)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="洛克王国世界本地地图工具")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    run(smoke_test=args.smoke_test)


if __name__ == "__main__":
    main()
