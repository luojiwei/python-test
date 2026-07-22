"""Ramer-Douglas-Peucker 折线简化算法。"""


def rdp_simplify(points, epsilon: float = 2.5):
    """Ramer-Douglas-Peucker polyline simplification.

    points: [(x, y), ...] ordered by x
    Returns simplified point list.
    """
    if len(points) < 3:
        return [list(p) for p in points]

    def perp_dist_sq(p, a, b):
        if a[0] == b[0] and a[1] == b[1]:
            dx, dy = p[0] - a[0], p[1] - a[1]
            return dx * dx + dy * dy
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        norm_sq = dx * dx + dy * dy
        cross = abs(dy * (a[0] - p[0]) - (a[1] - p[1]) * dx)
        return (cross * cross) / norm_sq

    epsilon_sq = epsilon * epsilon

    def rdp(pts):
        if len(pts) < 3:
            return list(pts)
        dmax = 0.0
        idx = 0
        for i in range(1, len(pts) - 1):
            d = perp_dist_sq(pts[i], pts[0], pts[-1])
            if d > dmax:
                dmax = d
                idx = i
        if dmax > epsilon_sq:
            left = rdp(pts[:idx + 1])
            right = rdp(pts[idx:])
            return left + right[1:]
        else:
            return [pts[0], pts[-1]]

    return rdp([(p[0], p[1]) for p in points])
