"""
maps.py — Carte unifiée des marqueurs ArUco de la salle robotique.

Un seul endroit de vérité pour les positions des marqueurs fixes.
Les deux nœuds qui consomment cette carte :

  - localization_node  : utilise TOUS les marqueurs pour recaler la pose
                         (plus de marqueurs visibles → meilleure précision)
  - navigation_node    : utilise UNIQUEMENT ceux marqués is_obstacle=True
                         pour la déviation latérale pendant les trajets

Convention d'IDs :
  0-9   : stations (destinations) — gérées séparément dans navigation_node
  10-49 : marqueurs de localisation pure (au mur, au plafond, etc.)
  50-99 : obstacles (meubles, machines fixes) — servent aussi à la localisation

Repère salle :
  origine = intersection de carreaux ; 0° = -Y ; +90° = +X ; sens trigo.
"""

# Format : ID -> (x, y, is_obstacle)
#   x, y           : position en mètres dans le repère salle
#   is_obstacle    : True si MARC doit dévier en passant à proximité
LANDMARKS_MAP = {
    # ── Marqueurs de localisation pure (positions connues, au mur) ──
    0:  (-2.07,  0.00,  False),
    1:  (-2.08,  1.07,  False),
    2:  ( 2.17,  1.05,  False),
    3:  (-0.66,  2.45,  False),
    4:  ( 2.17, -0.33,  False),
    5:  ( 0.96, -3.39,  False),

    # ── Obstacles (meubles, machines) — à compléter selon la salle ──
    # 50: ( 1.20,  0.50,  True),    # Ex: table à côté de NAO
    # 51: (-1.00, -2.00,  True),    # Ex: armoire
}


# ─────────────────────────────────────────────
#  Vues filtrées (helpers)
# ─────────────────────────────────────────────
def all_positions() -> dict[int, tuple[float, float]]:
    """Tous les marqueurs avec leur (x, y) — pour localisation."""
    return {mid: (x, y) for mid, (x, y, _) in LANDMARKS_MAP.items()}


def obstacle_positions() -> dict[int, tuple[float, float]]:
    """Uniquement les marqueurs d'obstacles — pour navigation."""
    return {mid: (x, y) for mid, (x, y, is_obs) in LANDMARKS_MAP.items() if is_obs}
