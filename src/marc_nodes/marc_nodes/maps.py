"""
maps.py — Carte de la salle robotique de MARC.

Trois données, un seul endroit de vérité :

  LANDMARKS_MAP : marqueurs ArUco fixes (mur, plafond) → position (x, y).
                  Consommé par localization_node pour recaler la pose.

  STATION_POS   : nom de station → position (x, y) de la station dans la salle.

  WAYPOINTS/EDGES : graphe de points de passage sûrs. mission_node planifie sur
                  ce graphe (Dijkstra) le chemin depuis la position courante de
                  MARC jusqu'à la cible (station ou point libre).

Repère salle :
  origine = intersection de carreaux choisie comme (0,0) ;
  0° = -Y ; +90° = +X ; sens trigonométrique.

⚠️ Les positions de stations et le graphe ci-dessous sont des EMPLACEMENTS
À RENSEIGNER avec des mesures réelles (mètre ruban depuis l'origine). Tant
qu'une valeur vaut None / le graphe est vide, mission_node le signale ou
retombe sur un trajet direct au lieu d'inventer une trajectoire.
"""

import heapq
import math

# ─────────────────────────────────────────────
#  MARQUEURS DE LOCALISATION (positions réelles connues)
# ─────────────────────────────────────────────
# ID ArUco → (x, y) en mètres. Servent UNIQUEMENT à la localisation.
LANDMARKS_MAP: dict[int, tuple[float, float]] = {
    0: (-2.07,  0.00),
    1: (-2.08,  1.07),
    2: ( 2.17,  1.05),
    3: (-0.66,  2.45),
    4: ( 2.17, -0.33),
    5: ( 0.96, -3.39),
}


# ─────────────────────────────────────────────
#  STATIONS — position (x, y) de chaque destination
# ─────────────────────────────────────────────
# None = à mesurer. mission_node refuse un trajet vers une station sans position
# NI parcours, plutôt que d'inventer des coordonnées.
STATION_POS: dict[str, tuple[float, float] | None] = {
    "nao":    (-1.6, 0),
    "vector": (-1.5, 4.9),
    "pepper": None,   # TODO
    "imp3d":  None,   # TODO
    "baxter": (1.8, 1.8),
    "bras":   (1.8, -0.6),
}


# ─────────────────────────────────────────────
#  GRAPHE DE WAYPOINTS — chemins sûrs dans la salle
# ─────────────────────────────────────────────
# Au lieu de tracer un parcours figé par station, on définit un GRAPHE de points
# de passage sûrs. mission_node planifie alors, depuis la position courante de
# MARC, le plus court chemin (Dijkstra) jusqu'à la cible — station ou point libre.
#
#   WAYPOINTS : nom de nœud → position (x, y) d'un point de passage sûr.
#   EDGES     : couples (nœud_a, nœud_b) reliés par un segment SANS obstacle.
#               Bidirectionnel ; le coût est la distance euclidienne.
#
# Hypothèse : les nœuds sont assez denses pour que, depuis tout endroit
# atteignable, le nœud le plus proche soit joignable en ligne droite sans
# obstacle (idem entre un nœud et la station/point visé). C'est cette densité
# qui garantit la sûreté des petits segments d'entrée et de sortie du graphe.
#
# ⚠️ À renseigner avec des positions réelles. Graphe vide → mission_node
# retombe sur un trajet direct (ligne droite), sans garantie d'évitement.
#
# Exemple (valeurs fictives) :
#   WAYPOINTS = {"home": (0.0, 0.0), "c1": (1.0, 0.0), "c2": (1.0, 1.0)}
#   EDGES     = [("home", "c1"), ("c1", "c2")]
WAYPOINTS: dict[str, tuple[float, float]] = {
    "origin": (0.0, 0.0),
    "B":   (-1.2,   0),
    "C":   (0.9, -0.6),
    "D":   (0, -1.2),
    "E":   (-1.2, -2.4),
    "F":   (1.2, -2),
    "G":   (0, 1.2),
    "H":   (0.3, 2.4),
    "I":   (0, 3.6),
}

EDGES: list[tuple[str, str]] = [
    ("origin", "B"),
    ("origin", "D"),
    ("origin", "C"),
    ("origin", "G"),
    ("D", "B"),
    ("G", "B"),
    ("D", "C"),
    ("G", "C"),
    ("D", "E"),
    ("D", "F"),
    ("H", "G"),
    ("I", "H"),
]


# ─────────────────────────────────────────────
#  CAP FINAL — orientation de MARC une fois arrivé à la station
# ─────────────────────────────────────────────
# Cap en degrés dans le repère salle (0° = -Y, +90° = +X, sens trigo), pour que
# MARC fasse face au bon endroit en arrivant (présenter le robot, faire face au
# visiteur…). None = pas d'orientation imposée (MARC s'arrête tel quel).
STATION_HEADING: dict[str, float | None] = {
    "nao":    90,
    "vector": 30,
    "pepper": None,
    "imp3d":  None,
    "baxter": 90,
    "bras":   -90,
}


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
def all_positions() -> dict[int, tuple[float, float]]:
    """Tous les marqueurs de localisation (id → (x, y))."""
    return dict(LANDMARKS_MAP)


def station_pos(name: str) -> tuple[float, float] | None:
    """Position (x, y) d'une station, ou None si non renseignée/inconnue."""
    return STATION_POS.get(name.lower())


def station_heading(name: str) -> float | None:
    """Cap final (degrés) à adopter à l'arrivée, ou None si non imposé."""
    return STATION_HEADING.get(name.lower())


# ── Planification de chemin sur le graphe de waypoints ──
def nearest_node(xy: tuple[float, float]) -> str | None:
    """Nœud du graphe le plus proche d'un point (x, y)."""
    if not WAYPOINTS:
        return None
    x, y = xy
    return min(WAYPOINTS,
               key=lambda n: (WAYPOINTS[n][0] - x) ** 2 + (WAYPOINTS[n][1] - y) ** 2)


def _adjacency() -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = {n: [] for n in WAYPOINTS}
    for a, b in EDGES:
        if a in WAYPOINTS and b in WAYPOINTS:
            d = math.dist(WAYPOINTS[a], WAYPOINTS[b])
            adj[a].append((b, d))
            adj[b].append((a, d))
    return adj


def _dijkstra(adj, start: str, goal: str) -> list[str] | None:
    dist = {start: 0.0}
    prev: dict[str, str] = {}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if goal not in dist:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def plan_path(start_xy: tuple[float, float],
              goal_xy: tuple[float, float]) -> list[tuple[float, float]] | None:
    """Suite de positions (x, y) des nœuds, de l'entrée du graphe la plus proche
    de start_xy jusqu'à la sortie la plus proche de goal_xy.

    Retourne None si le graphe est vide ou si aucun chemin n'existe — l'appelant
    retombe alors sur un trajet direct (ligne droite) vers goal_xy.
    """
    if not WAYPOINTS or not EDGES:
        return None
    entry = nearest_node(start_xy)
    exit_ = nearest_node(goal_xy)
    if entry is None or exit_ is None:
        return None
    if entry == exit_:
        return [WAYPOINTS[entry]]
    nodes = _dijkstra(_adjacency(), entry, exit_)
    if nodes is None:
        return None
    return [WAYPOINTS[n] for n in nodes]
