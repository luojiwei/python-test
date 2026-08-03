"""review_dialog.py — 标注审核弹窗 (PySide6: 拖拽补框 / 列表管理 / 翻页审核)"""

from pathlib import Path

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                QLabel, QListWidget, QComboBox, QMessageBox,
                                QSplitter, QWidget, QGroupBox, QSizePolicy,
                                QLineEdit)
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QFont, QImage
from PySide6.QtCore import Qt, QRectF, QPointF

from config import load_reviewed_stems

CLASS_NAMES: dict[int, str] = {0: "怪物", 1: "绳子上", 2: "绳子下", 3: "梯子上", 4: "梯子下"}
CLASS_COLORS: list[str] = ["#00DD00", "#FF4444", "#FF8800", "#4488FF", "#AA00EE"]


class ImageCanvas(QLabel):
    """可拖拽画框的图片显示组件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self._pixmap_orig: QPixmap | None = None
        self._boxes: list[tuple[float, float, float, float, int]] = []
        self._selected_idx: int = -1
        self._drawing: bool = False
        self._draw_start: QPointF | None = None
        self._draw_end: QPointF | None = None
        self._offset_x: float = 0
        self._offset_y: float = 0
        self._on_box_added = None  # callback

    def set_image(self, path: Path, boxes: list[tuple[float, float, float, float, int]]) -> None:
        """加载图片并设置标注框。"""
        pix = QPixmap(str(path))
        if pix.isNull():
            self._pixmap_orig = None
            self._boxes = []
            self._selected_idx = -1
            self.update()
            return

        # 缩放到画布宽度（宽度填满，保持比例）
        cw = self.width()
        ch = self.height()
        # 初始布局未完成时用父控件尺寸
        if cw < 100 and self.parent():
            cw = self.parent().width()
            ch = self.parent().height()
        if cw < 10 or ch < 10:
            cw, ch = 780, 620
        ratio = cw / pix.width()  # 按宽度填满，不限制高度
        new_w = int(pix.width() * ratio)
        new_h = int(pix.height() * ratio)
        self._pixmap_orig = pix.scaled(new_w, new_h, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation)
        self._offset_x = 0  # 宽度已填满，左侧无偏移
        self._offset_y = max(0, (ch - new_h) / 2)
        self._boxes = boxes
        self._selected_idx = -1
        self.update()

    def get_boxes(self) -> list[tuple[float, float, float, float, int]]:
        return self._boxes

    def keyPressEvent(self, event):
        # 转发给父对话框处理快捷键
        if self.parent():
            self.parent().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def clear(self) -> None:
        """清空画布。"""
        self._pixmap_orig = None
        self._boxes = []
        self._selected_idx = -1
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 绘制图片
        if self._pixmap_orig and not self._pixmap_orig.isNull():
            pix_w = self._pixmap_orig.width()
            pix_h = self._pixmap_orig.height()
            painter.drawPixmap(int(self._offset_x), int(self._offset_y),
                              self._pixmap_orig)

            # 绘制框
            colors = CLASS_COLORS
            for i, (x1, y1, x2, y2, cls_id) in enumerate(self._boxes):
                selected = (i == self._selected_idx)
                color = QColor(colors[cls_id % len(colors)])
                pen_w = 3 if selected else 2
                painter.setPen(QPen(QColor("#FF0000") if selected else color, pen_w))

                rect_x = x1 * pix_w + self._offset_x
                rect_y = y1 * pix_h + self._offset_y
                rect_w = (x2 - x1) * pix_w
                rect_h = (y2 - y1) * pix_h
                painter.drawRect(QRectF(rect_x, rect_y, rect_w, rect_h))

                name = CLASS_NAMES.get(cls_id, f"cls{cls_id}")
                font = QFont("Microsoft YaHei", 9)
                font.setBold(selected)
                painter.setFont(font)
                painter.setPen(QPen(QColor("#FF0000") if selected else color))
                painter.drawText(int(rect_x + 2), int(rect_y - 4), name)

        # 绘制拖拽预览
        if self._drawing and self._draw_start and self._draw_end:
            painter.setPen(QPen(QColor("#FFFF00"), 2, Qt.DashLine))
            painter.drawRect(QRectF(self._draw_start, self._draw_end))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drawing = True
            self._draw_start = event.position()
            self._draw_end = event.position()

    def mouseMoveEvent(self, event):
        if self._drawing:
            self._draw_end = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if not self._drawing or self._pixmap_orig is None:
            self._drawing = False
            return
        self._drawing = False
        p1 = self._draw_start
        p2 = event.position()

        # 检查框是否太小
        if abs(p2.x() - p1.x()) < 10 or abs(p2.y() - p1.y()) < 10:
            self.update()
            return

        # 转换为归一化坐标
        pix_w = self._pixmap_orig.width()
        pix_h = self._pixmap_orig.height()
        x1 = (min(p1.x(), p2.x()) - self._offset_x) / pix_w
        x2 = (max(p1.x(), p2.x()) - self._offset_x) / pix_w
        y1 = (min(p1.y(), p2.y()) - self._offset_y) / pix_h
        y2 = (max(p1.y(), p2.y()) - self._offset_y) / pix_h
        x1 = max(0.0, min(1.0, x1))
        x2 = max(0.0, min(1.0, x2))
        y1 = max(0.0, min(1.0, y1))
        y2 = max(0.0, min(1.0, y2))

        self._boxes.append((x1, y1, x2, y2, 0))
        self._selected_idx = len(self._boxes) - 1
        if self._on_box_added:
            self._on_box_added()
        self.update()


class ReviewDialog(QDialog):
    """弹窗审核标注结果。

    快捷键: Left/Right 翻页 | D 删除选中 | A 通过 | Esc 关闭 | 鼠标拖拽补框
    """

    def __init__(self, parent, images: list[Path],
                 labels_train: Path, labels_val: Path,
                 on_close=None, reviewed_stems: set[str] | None = None) -> None:
        super().__init__(parent)
        self._all_images = images  # 全部图片
        self._reviewed_stems_before = reviewed_stems or set()  # 进来时已审查的
        self._filtered_images = images  # 当前筛选后的图片
        self._filter_mode = "unreviewed"  # "unreviewed" | "reviewed"
        self.labels_train = labels_train
        self.labels_val = labels_val
        self.on_close = on_close
        self._current_idx: int = 0
        self._modified: bool = False
        self._boxes: list[tuple[float, float, float, float, int]] = []
        self._reviewed_stems: set[str] = set()  # 本次审查过的图片

        self._apply_filter()
        total = len(self._all_images)
        unreviewed = len([1 for img in self._all_images if img.stem not in self._reviewed_stems_before])
        self.setWindowTitle(f"审核标注 — 共 {total} 张，待审查 {unreviewed}")
        self.resize(1280, 720)
        self.setMinimumSize(1000, 600)
        self.setModal(True)

        self._build_ui()
        self._update_toggle_labels()
        self._load_image(0)

        # 全局键盘拦截：防止列表/下拉框吃掉快捷键
        self._listbox.installEventFilter(self)
        self._class_combo.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截子控件快捷键，交给 dialog 统一处理。"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            key = event.key()
            # Enter/Return 按键放行（用于跳转输入框等）
            if key in (Qt.Key_Return, Qt.Key_Enter):
                return False
            # 只拦截快捷键，放过普通字符输入
            is_shortcut = (
                Qt.Key_Left <= key <= Qt.Key_Down  # 方向键
                or Qt.Key_1 <= key <= Qt.Key_5     # 数字改类别
                or key in (Qt.Key_A, Qt.Key_D, Qt.Key_Escape)  # 通过/删除/关闭
                or (key == Qt.Key_S and event.modifiers() == Qt.ControlModifier)
            )
            if is_shortcut:
                self.keyPressEvent(event)
                return True
        return super().eventFilter(obj, event)

    def _jump_to_page(self) -> None:
        """跳转到指定页码。"""
        try:
            page = int(self._jump_edit.text()) - 1  # 用户输入从1开始
        except ValueError:
            return
        if 0 <= page < len(self._filtered_images):
            self._save_current()
            self._load_image(page)
            self._jump_edit.clear()
            self._jump_edit.clearFocus()  # 避免 Enter 事件触发其他控件
        else:
            self._jump_edit.selectAll()

    def _apply_filter(self) -> None:
        """根据筛选模式更新图片列表。"""
        if self._filter_mode == "unreviewed":
            self._filtered_images = [img for img in self._all_images
                                     if img.stem not in self._reviewed_stems_before
                                     and img.stem not in self._reviewed_stems]
        elif self._filter_mode == "current_reviewed":
            # 本轮已审核的
            self._filtered_images = [img for img in self._all_images
                                     if img.stem in self._reviewed_stems]
        else:  # history = 所有历史已审查的
            reviewed_all = load_reviewed_stems() | self._reviewed_stems
            self._filtered_images = [img for img in self._all_images
                                     if img.stem in reviewed_all]

    def _toggle_filter(self, mode: str) -> None:
        """切换筛选模式。"""
        if self._filter_mode == mode:
            return
        self._save_current()
        self._filter_mode = mode
        self._apply_filter()
        self._update_toggle_labels()
        self._load_image(0)

    def _update_toggle_labels(self) -> None:
        """更新切换标签上的数量。"""
        unreviewed = len([1 for img in self._all_images
                          if img.stem not in self._reviewed_stems_before
                          and img.stem not in self._reviewed_stems])
        current = len(self._reviewed_stems)
        reviewed_all = load_reviewed_stems() | self._reviewed_stems
        reviewed = len([1 for img in self._all_images if img.stem in reviewed_all])
        self._btn_unreviewed.setText(f"待审查 ({unreviewed})")
        self._btn_unreviewed.setChecked(self._filter_mode == "unreviewed")
        self._btn_current.setText(f"已审核 ({current})")
        self._btn_current.setChecked(self._filter_mode == "current_reviewed")
        self._btn_reviewed.setText(f"历史审核 ({reviewed})")
        self._btn_reviewed.setChecked(self._filter_mode == "history")

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.setSpacing(4)

        # 顶部
        top = QHBoxLayout()
        self._info_var = QLabel("0 / 0")
        font = self._info_var.font()
        font.setBold(True)
        font.setPointSize(10)
        self._info_var.setFont(font)
        top.addWidget(self._info_var)
        self._status_var = QLabel("")
        self._status_var.setStyleSheet("color: gray;")
        top.addWidget(self._status_var)
        top.addStretch()

        # 筛选切换标签
        toggle_style = """
            QPushButton { padding: 4px 12px; border: 1px solid #999; border-radius: 4px; }
            QPushButton:checked { background: #1976D2; color: white; border-color: #1976D2; }
        """
        self._btn_unreviewed = QPushButton("待审查")
        self._btn_unreviewed.setCheckable(True)
        self._btn_unreviewed.setChecked(True)
        self._btn_unreviewed.setStyleSheet(toggle_style)
        self._btn_unreviewed.clicked.connect(lambda: self._toggle_filter("unreviewed"))
        top.addWidget(self._btn_unreviewed)

        self._btn_current = QPushButton("已审核")
        self._btn_current.setCheckable(True)
        self._btn_current.setStyleSheet(toggle_style)
        self._btn_current.clicked.connect(lambda: self._toggle_filter("current_reviewed"))
        top.addWidget(self._btn_current)

        self._btn_reviewed = QPushButton("历史审核")
        self._btn_reviewed.setCheckable(True)
        self._btn_reviewed.setStyleSheet(toggle_style)
        self._btn_reviewed.clicked.connect(lambda: self._toggle_filter("history"))
        top.addWidget(self._btn_reviewed)
        top.addSpacing(12)

        btn_approve = QPushButton("通过 (A)")
        btn_approve.clicked.connect(self._approve)
        top.addWidget(btn_approve)
        main_layout.addLayout(top)

        # 主体：左侧画布 + 右侧列表
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStretchFactor(0, 16)
        splitter.setStretchFactor(1, 1)

        self._canvas = ImageCanvas()
        self._canvas._on_box_added = self._on_canvas_box_added
        self._canvas.setFixedWidth(980)
        self._canvas.setMinimumHeight(551)  # 16:9 @ 980px
        self._canvas.setStyleSheet("background-color: #2D2D2D;")
        splitter.addWidget(self._canvas)

        # 右侧
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(4, 0, 0, 0)
        side_layout.addWidget(QLabel("标注列表"))

        self._listbox = QListWidget()
        self._listbox.currentRowChanged.connect(self._on_list_select)
        side_layout.addWidget(self._listbox)

        # 列表操作
        list_op = QGroupBox("操作")
        lo_layout = QVBoxLayout(list_op)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("改为:"))
        self._class_combo = QComboBox()
        self._class_combo.addItems(list(CLASS_NAMES.values()))
        row1.addWidget(self._class_combo)
        btn_change = QPushButton("修改")
        btn_change.clicked.connect(self._change_class)
        row1.addWidget(btn_change)
        lo_layout.addLayout(row1)
        btn_del = QPushButton("删除 (D)")
        btn_del.clicked.connect(self._delete_selected)
        lo_layout.addWidget(btn_del)
        side_layout.addWidget(list_op)
        splitter.addWidget(side)
        main_layout.addWidget(splitter)

        # 底部导航
        bottom = QHBoxLayout()
        btn_prev = QPushButton("上一张 (Left)")
        btn_prev.clicked.connect(self._prev)
        bottom.addWidget(btn_prev)

        self._jump_label = QLabel("跳至:")
        bottom.addWidget(self._jump_label)
        from PySide6.QtWidgets import QLineEdit
        self._jump_edit = QLineEdit()
        self._jump_edit.setFixedWidth(50)
        self._jump_edit.setPlaceholderText("页码")
        self._jump_edit.returnPressed.connect(self._jump_to_page)
        bottom.addWidget(self._jump_edit)

        btn_next = QPushButton("下一张 (Right)")
        btn_next.clicked.connect(self._next)
        bottom.addWidget(btn_next)
        bottom.addStretch()
        hint = QLabel("鼠标拖拽画框 | 列表点选 | D=删除 | A=通过 | Left/Right=翻页")
        hint.setStyleSheet("color: gray;")
        bottom.addWidget(hint)
        main_layout.addLayout(bottom)

    # ============================================================
    # 图片加载
    # ============================================================
    def _get_label_path(self, img_stem: str) -> Path:
        """获取标注文件路径，优先 train，其次 val。"""
        p = self.labels_train / f"{img_stem}.txt"
        if p.exists():
            return p
        return self.labels_val / f"{img_stem}.txt"

    def _load_image(self, idx: int) -> None:
        if not self._filtered_images:
            self._canvas.clear()
            self._canvas.setText("暂无图片")
            self._canvas.setStyleSheet("background-color: #2D2D2D; color: #999; font: 18px 'Microsoft YaHei';")
            self._canvas.setAlignment(Qt.AlignCenter)
            self._canvas.update()
            self._info_var.setText("0 / 0")
            self._status_var.setText("")
            self._listbox.clear()
            return
        self._canvas.setStyleSheet("background-color: #2D2D2D;")
        self._canvas.setText("")
        self._canvas.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._current_idx = max(0, min(idx, len(self._filtered_images) - 1))
        img_path = self._filtered_images[self._current_idx]

        # 读取标注框（归一化坐标）
        boxes: list[tuple[float, float, float, float, int]] = []
        label_path = self._get_label_path(img_path.stem)
        if label_path.exists():
            for line in label_path.read_text(encoding="utf-8").strip().split("\n"):
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                cls_id = int(parts[0])
                cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                x1 = max(0.0, cx - w / 2)
                y1 = max(0.0, cy - h / 2)
                x2 = min(1.0, cx + w / 2)
                y2 = min(1.0, cy + h / 2)
                boxes.append((x1, y1, x2, y2, cls_id))

        self._boxes = boxes
        self._canvas.set_image(img_path, boxes)
        self._canvas._selected_idx = -1
        self._refresh_list()
        self._info_var.setText(f"{self._current_idx + 1} / {len(self._filtered_images)}")
        self._status_var.setText(f"文件: {img_path.name}  |  框数: {len(boxes)}")
        self._modified = False

    # ============================================================
    # 列表
    # ============================================================
    def _refresh_list(self) -> None:
        self._listbox.blockSignals(True)
        self._listbox.clear()
        for i, (x1, y1, x2, y2, cls_id) in enumerate(self._boxes):
            name = CLASS_NAMES.get(cls_id, f"cls{cls_id}")
            self._listbox.addItem(f"  {i+1}. {name}")

        sel = self._canvas._selected_idx
        if sel >= 0 and sel < len(self._boxes):
            self._listbox.setCurrentRow(sel)
        self._listbox.blockSignals(False)

    def _on_list_select(self, idx: int) -> None:
        self._canvas._selected_idx = idx
        if idx >= 0 and idx < len(self._boxes):
            cls_id = self._boxes[idx][4]
            name = CLASS_NAMES.get(cls_id, "怪物")
            self._class_combo.setCurrentText(name)
        self._canvas.update()

    def _on_canvas_box_added(self) -> None:
        self._refresh_list()
        self._modified = True
        self._status_var.setText(
            f"文件: {self._filtered_images[self._current_idx].name}  |  "
            f"框数: {len(self._canvas.get_boxes())}  [已修改]")

    def _change_class(self) -> None:
        sel = self._canvas._selected_idx
        boxes = self._canvas.get_boxes()
        if sel < 0 or sel >= len(boxes):
            return
        new_name = self._class_combo.currentText()
        new_id = 0
        for kid, name in CLASS_NAMES.items():
            if name == new_name:
                new_id = kid
                break
        x1, y1, x2, y2, _ = boxes[sel]
        boxes[sel] = (x1, y1, x2, y2, new_id)
        self._refresh_list()
        self._canvas.update()
        self._modified = True
        self._status_var.setText(
            f"文件: {self._filtered_images[self._current_idx].name}  |  "
            f"框数: {len(boxes)}  [已修改]")

    def _quick_set_class(self, cls_id: int) -> None:
        """快捷键设置选中框类别。"""
        sel = self._canvas._selected_idx
        boxes = self._canvas.get_boxes()
        if sel < 0 or sel >= len(boxes):
            return
        x1, y1, x2, y2, _ = boxes[sel]
        boxes[sel] = (x1, y1, x2, y2, cls_id)
        self._canvas._selected_idx = sel
        self._refresh_list()
        self._canvas.update()
        self._modified = True
        name = CLASS_NAMES.get(cls_id, "怪物")
        self._status_var.setText(
            f"文件: {self._filtered_images[self._current_idx].name}  |  "
            f"框数: {len(boxes)}  [{name}]")

    def _delete_selected(self) -> None:
        sel = self._canvas._selected_idx
        boxes = self._canvas.get_boxes()
        if sel < 0 or sel >= len(boxes):
            return
        boxes.pop(sel)
        self._canvas._selected_idx = min(sel, len(boxes) - 1)
        self._refresh_list()
        self._canvas.update()
        self._modified = True
        self._status_var.setText(
            f"文件: {self._filtered_images[self._current_idx].name}  |  "
            f"框数: {len(boxes)}  [已修改]")

    # ============================================================
    # 操作
    # ============================================================
    def _approve(self) -> None:
        self._reviewed_stems.add(self._filtered_images[self._current_idx].stem)
        self._save_current(force=True)
        if self._filter_mode == "unreviewed":
            self._apply_filter()
            self._update_toggle_labels()
            # 当前图片已被移除，列表自动前移，保持当前索引即可看到下一个
            if self._filtered_images:
                self._load_image(min(self._current_idx, len(self._filtered_images) - 1))
            else:
                self._load_image(0)  # 全部审完
        else:
            self._next()

    def _next(self) -> None:
        self._save_current()
        if self._current_idx < len(self._filtered_images) - 1:
            self._load_image(self._current_idx + 1)

    def _prev(self) -> None:
        self._save_current()
        if self._current_idx > 0:
            self._load_image(self._current_idx - 1)

    def _save_current(self, force: bool = False) -> None:
        if not force and not self._modified:
            return
        if not self._filtered_images:
            return
        img_path = self._filtered_images[self._current_idx]
        label_path = self._get_label_path(img_path.stem)

        lines: list[str] = []
        for x1, y1, x2, y2, cls_id in self._canvas.get_boxes():
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            w = max(0.0001, x2 - x1)
            h = max(0.0001, y2 - y1)
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

        label_path.write_text("\n".join(lines) + "\n" if lines else "",
                              encoding="utf-8")
        self._modified = False

    def _close(self) -> None:
        # 关闭时自动保存当前修改
        self._save_current(force=True)
        if self.on_close:
            self.on_close(self._reviewed_stems)
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self._prev()
        elif event.key() == Qt.Key_Right:
            self._next()
        elif event.key() == Qt.Key_D:
            self._delete_selected()
        elif event.key() == Qt.Key_S and event.modifiers() == Qt.ControlModifier:
            self._save_current()
            self._status_var.setText(
                f"文件: {self._filtered_images[self._current_idx].name}  |  "
                f"框数: {len(self._canvas.get_boxes())}  [已保存]")
        elif Qt.Key_1 <= event.key() <= Qt.Key_5:
            # 数字键 1-5 快速设置选中框类别
            cls_id = event.key() - Qt.Key_1
            self._quick_set_class(cls_id)
        elif event.key() == Qt.Key_A:
            self._approve()
        elif event.key() == Qt.Key_Escape:
            self._close()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return  # 阻止 Enter 触发默认按钮
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """X 关闭按钮也走统一关闭逻辑。"""
        self._close()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时重新加载当前图片
        if self._filtered_images and self._current_idx < len(self._filtered_images):
            img_path = self._filtered_images[self._current_idx]
            self._canvas.set_image(img_path, self._boxes)
