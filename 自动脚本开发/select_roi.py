"""弹出图片让用户框选角色名位置，输出坐标"""
import cv2, numpy as np
from pathlib import Path

pp = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/test_output/snap_for_mark.png")
img = cv2.imdecode(np.frombuffer(pp.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

print("请在图片上用鼠标框选角色名'爱吃姜的小罗'，按 ENTER 确认，ESC 取消")
roi = cv2.selectROI("框选角色名", img, False)
cv2.destroyAllWindows()

if roi[2] > 0 and roi[3] > 0:
    x1 = int(roi[0])
    y1 = int(roi[1])
    x2 = x1 + int(roi[2])
    y2 = y1 + int(roi[3])
    print(f"\n角色名区域: ({x1},{y1})-({x2},{y2}) {int(roi[2])}x{int(roi[3])}")
else:
    print("已取消")
