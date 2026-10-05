"""
settings.py — Central configuration for the Coimbatore Smart City simulation.

BCSE355L Smart Cities · VIT Vellore
Team: Aditya Saxena (24BCE1633) · Narayanan Subramanian (24BCE1699)

Every constant used by the simulation lives here as a field of the :class:`Config`
dataclass, so a scenario is fully described by one object and can be hashed into a
run manifest. ``Config()`` is the shipped baseline.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type hints only
    from src.models.ecology import EcologyParams


# ══════════════════════════════════════════════════════════════════════════
# STATE-OF-CHARGE DISPLAY BANDS
# ══════════════════════════════════════════════════════════════════════════
# These are the single definition of every SOC band boundary in the project. They
# drive, from one place:
#
#   * ``Bus.condition``              -> the operator-facing status text
#   * ``Bus.soc_color``              -> the fleet-table marker colour
#   * the map's JS ``socColour()``   -> the on-map marker colour
#   * the map legend                 -> the printed band labels
#
# They are deliberately **module-level constants rather than ``Config`` fields**.
# ``Bus`` holds no ``Config`` reference, so declaring them as scenario fields would
# recreate the exact defect this replaced: an authoritative-looking field that
# silently does nothing. Keeping them here means there is one source and no
# fallback path that can diverge.
#
# Two independent scales exist on purpose:
#   * status bands  - operational urgency, four named states
#   * colour bands  - a smoother four-step ramp, deliberately offset from the
#                     status bands so "Monitor SOC" (below 45%) is still amber
#                     rather than already orange.

#: Below this the bus is "Critical SOC" and orange/red.
SOC_CRITICAL_PCT: float = 20.0
#: Below this the bus is "Swap priority". Matches ``Config.SOC_SWAP_THRESHOLD``.
SOC_SWAP_PCT: float = 30.0
#: Below this the bus is "Monitor SOC"; above it, "In service".
SOC_MONITOR_PCT: float = 45.0

#: Marker-colour ramp, ordered low to high: critical, low, mid, healthy.
SOC_COLOUR_SCALE: Tuple[str, ...] = ("#EF4444", "#F97316", "#EAB308", "#22C55E")
#: Band boundaries matching :data:`SOC_COLOUR_SCALE`, low to high.
SOC_COLOUR_LOW_PCT: float = 20.0
SOC_COLOUR_MID_PCT: float = 40.0
SOC_COLOUR_HIGH_PCT: float = 70.0

#: Human labels for the status bands, low to high. ``Bus.condition`` returns these.
SOC_STATUS_LABELS: Tuple[str, ...] = (
    "Critical SOC", "Swap priority", "Monitor SOC", "In service",
)

#: Labels for the operational states that take priority over the SOC ladder. Kept here
#: so the dashboard's condition filter cannot drift from what ``Bus.condition`` returns.
BUS_STATE_LABELS: Tuple[str, ...] = (
    "Charging / battery swap",
    "At depot",
    "Diverting to swap station",
)

#: Every value ``Bus.condition`` can return, in a stable display order: the SOC ladder
#: first, then the operational states. The dashboard filter is built from this, so adding
#: a state in one place adds it in both.
BUS_CONDITION_LABELS: Tuple[str, ...] = SOC_STATUS_LABELS + BUS_STATE_LABELS


@dataclass
class Config:
    # ─────────────────────────────────────────────
    # UKKADAM LAKE (PERIYAKULAM), COIMBATORE
    # OSM-verified exact boundary coordinates
    # ─────────────────────────────────────────────
    LAKE_AREA_M2: float = 1_295_000.0        # m² (1.295 km²)
    LAKE_DEPTH_M: float = 5.82               # average depth in metres
    LAKE_LAT: float = 10.9835                # Ukkadam Lake geographic centre
    LAKE_LON: float = 76.9554

    # Exact OpenStreetMap polygon for Ukkadam Lake (lat, lon)
    LAKE_POLYGON: List[Tuple[float, float]] = field(default_factory=lambda: [
        (10.97998, 76.94508),
        (10.97911, 76.94594),
        (10.97888, 76.94771),
        (10.97902, 76.95023),
        (10.97855, 76.95097),
        (10.97868, 76.95239),
        (10.97842, 76.95568),
        (10.97825, 76.95880),
        (10.97980, 76.96079),
        (10.98153, 76.96114),
        (10.98474, 76.96135),
        (10.98581, 76.96128),
        (10.98689, 76.96119),
        (10.98829, 76.96102),
        (10.98875, 76.96051),
        (10.98804, 76.95820),
        (10.98703, 76.95650),
        (10.98579, 76.95394),
        (10.98441, 76.95002),
        (10.98320, 76.94919),
        (10.98209, 76.94763),
        (10.98025, 76.94692),
        (10.98008, 76.94512),
        (10.97998, 76.94508),
    ])

    # Floating Solar Polygon (15% surface area concentrated on northeast calm waters near depot)
    SOLAR_ARRAY_POLYGON: List[Tuple[float, float]] = field(default_factory=lambda: [
        (10.98450, 76.95500),
        (10.98750, 76.95800),
        (10.98780, 76.96050),
        (10.98400, 76.96080),
        (10.98250, 76.95800),
        (10.98320, 76.95550),
        (10.98450, 76.95500),
    ])

    # ─────────────────────────────────────────────
    # FLOATING SOLAR — SCENARIO PARAMETERS
    # Coverage options: 10%, 15% (default), 20%
    # ─────────────────────────────────────────────
    # The default scenario's coverage. Deliberately set equal to
    # ECOLOGICAL_CAP_PCT, but a distinct concept: that one is the policy ceiling
    # used to flag over-cap scenarios, this one is what the app starts on.
    DEFAULT_COVERAGE_PCT: float = 15.0       # % of lake surface
    PANEL_AREA_M2_PER_MWP: float = 15_000.0 # 1.5 ha / MWp
    #: The 15% figure is a policy cap, enforced by :func:`check_ecology` and surfaced
    #: as a CRITICAL status. The ecological model independently predicts the minimum
    #: dissolved oxygen at each coverage; the dashboard reports both.
    ECOLOGICAL_CAP_PCT: float = 15.0

    # Computed capacities:
    # 10% → 1,295,000 × 0.10 / 15,000 ≈ 8.63 MWp
    # 15% → 1,295,000 × 0.15 / 15,000 ≈ 12.95 ≈ 13 MWp
    # 20% → 1,295,000 × 0.20 / 15,000 ≈ 17.27 MWp
    SCENARIO_COVERAGE_OPTIONS: List[float] = field(default_factory=lambda: [10.0, 15.0, 20.0])
    #: Additional non-compliant coverages offered only in the stress-test panel, so
    #: that the mechanical-aeration mitigation path can actually be demonstrated.
    STRESS_COVERAGE_OPTIONS: List[float] = field(default_factory=lambda: [25.0, 30.0, 40.0])

    #: Measured plane-of-array specific yield for Coimbatore, kWh/kWp/yr (SRRA/MNRE).
    ANNUAL_YIELD_KWH_PER_KWP: float = 1_600.0
    #: Relative yield premium of a floating array over an identical ground mount.
    #: Derived from the cell-temperature derate in ``algorithms.solar`` at Coimbatore
    #: ambient temperatures (floating cell rise 8 °C versus 22 °C ground-mounted, at
    #: -0.4 %/°C), which gives roughly 5-8 %. The previous blanket 1.10 was not
    #: derived from the temperature model and double-counted the cooling effect that
    #: the hourly model already applies.
    COOLING_BONUS_FACTOR: float = 1.06

    # Hourly BASE generation profile for 13 MWp (kW) — exact project spec values
    BASE_GENERATION_KW: Dict[int, float] = field(default_factory=lambda: {
        0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0,
        6: 200,
        7: 800,
        8: 2_500,
        9: 5_000,
        10: 8_000,
        11: 10_500,
        12: 12_000,   # ← Peak solar generation
        13: 12_000,
        14: 11_000,
        15: 9_000,
        16: 6_500,
        17: 3_500,
        18: 1_000,
        19: 0, 20: 0, 21: 0, 22: 0, 23: 0,
    })
    MAX_GENERATION_KW: float = 13_000.0     # 13 MWp system limit

    # ─────────────────────────────────────────────
    # ELECTRIC BUS FLEET
    # ─────────────────────────────────────────────
    NUM_BUSES: int = 260
    BATTERY_CAPACITY_KWH: float = 300.0     # per bus
    #: Traction energy per bus per service day **at TRAFFIC_INTENSITY_FACTOR = 1.0**.
    #: The traffic slider scales this figure, so the quoted 219 kWh is only valid at
    #: normal traffic. The hourly shape is normalised so that the profile sums to
    #: exactly this value, rather than inflating it by the peak/off-peak factors.
    DAILY_CONSUMPTION_KWH: float = 219.0

    #: Service window in hours. Buses are at the depot outside it and draw no
    #: traction power. Replaces the previous integer ``OPERATING_HOURS``, which
    #: disagreed with the 05:00-22:30 window the fleet update actually enforced.
    SERVICE_START_HOUR: float = 5.0
    SERVICE_END_HOUR: float = 22.0

    SWAP_TIME_MIN: int = 7                  # 5-10 minutes; completes within one 30-min step
    TRAFFIC_INTENSITY_FACTOR: float = 1.0   # 1.0 normal; >1 means heavier traffic/load
    CLOUD_COVER_MULTIPLIER: float = 1.0     # scenario multiplier for generated cloud cover

    # Peak traffic hours (passenger demand × 1.5)
    PEAK_HOURS: List[int] = field(default_factory=lambda: [6, 7, 8, 9, 17, 18, 19, 20])
    OFF_PEAK_FACTOR: float = 0.8
    PEAK_FACTOR: float = 1.5

    #: State of charge below which a swap is approved even when no solar surplus is
    #: available, so energy scarcity can never strand a vehicle.
    CRITICAL_SOC_PCT: float = 15.0

    # SOC thresholds
    #: Triggers swap candidacy. Distinct from the display bands below, which only
    #: classify a bus for the operator.
    SOC_SWAP_THRESHOLD: float = 30.0        # % — triggers swap candidacy
    SOC_INITIAL_MIN: float = 55.0
    SOC_INITIAL_MAX: float = 100.0

    # ─────────────────────────────────────────────
    # SIMULATION TIME RESOLUTION (30-minute intervals)
    # ─────────────────────────────────────────────
    TIME_STEP_HOURS: float = 0.5            # 30 minutes per simulation tick

    # Steps per simulated day are *derived* from TIME_STEP_HOURS in
    # ``src.simulation.engine`` (``STEPS_PER_DAY``). This field used to exist as well,
    # which gave the timestep two independent definitions that could silently disagree.

    # ─────────────────────────────────────────────
    # TNSTC BUS ROUTES (Street-Exact OSRM Network — No Building Clipping)
    # ─────────────────────────────────────────────
    ROUTES: Dict[str, dict] = field(default_factory=lambda: {
        "Route 69": {
            "color": "#3498DB",
            "name": "Ukkadam Depot → Pappampatti",
            "buses_allocated": 90,
            "stop_names": ["Ukkadam Depot", "Town Hall", "Singanallur", "Ondipudur", "Pappampatti"],
            "stop_waypoint_indices": [0, 12, 23, 36, 53],
            "waypoints": [
                (10.9881, 76.96188),
                (10.98791, 76.9615),
                (10.9884, 76.96495),
                (10.98916, 76.96485),
                (10.99414, 76.96594),
                (10.99474, 76.96915),
                (10.99545, 76.97564),
                (10.99529, 76.9744),
                (10.9963, 76.97922),
                (10.99861, 76.98473),
                (10.99844, 76.98531),
                (10.99806, 76.98839),
                (10.99799, 76.99047),
                (10.99734, 76.99312),
                (10.99691, 76.99128),
                (10.99735, 76.99414),
                (10.99716, 76.9965),
                (10.99713, 77.00278),
                (10.99698, 77.00663),
                (10.99749, 77.01082),
                (10.99812, 77.01584),
                (10.99926, 77.02377),
                (11.00147, 77.02659),
                (11.0002, 77.02663),
                (11.00068, 77.02963),
                (11.0014, 77.03392),
                (11.00322, 77.04182),
                (11.00332, 77.04907),
                (11.00207, 77.0563),
                (11.0022, 77.05845),
                (10.9977, 77.05827),
                (10.99351, 77.05615),
                (10.99351, 77.05615),
                (10.99529, 77.05746),
                (10.99549, 77.06095),
                (11.00147, 77.06664),
                (11.00693, 77.07015),
                (11.00774, 77.07111),
                (11.00892, 77.07744),
                (11.00793, 77.08076),
                (11.00353, 77.08212),
                (10.99989, 77.08259),
                (10.99677, 77.0825),
                (10.99369, 77.08488),
                (10.99229, 77.08615),
                (10.99067, 77.0875),
                (10.98593, 77.08944),
                (10.98027, 77.09097),
                (10.9758, 77.09077),
                (10.97331, 77.09202),
                (10.96704, 77.0974),
                (10.9633, 77.09914),
                (10.95987, 77.10111),
                (10.95938, 77.10142),
            ],
        },
        "Route 1": {
            "color": "#E67E22",
            "name": "Gandhipuram Hub → Ondipudur",
            "buses_allocated": 70,
            "stop_names": ["Gandhipuram Hub", "Avinashi Road", "Peelamedu", "Hope College", "Ondipudur Depot"],
            "stop_waypoint_indices": [0, 18, 33, 45, 55],
            "waypoints": [
                (11.01617, 76.96929),
                (11.01642, 76.97028),
                (11.01623, 76.97196),
                (11.01813, 76.97771),
                (11.01822, 76.97977),
                (11.01587, 76.98055),
                (11.01417, 76.98121),
                (11.01423, 76.98532),
                (11.01156, 76.98726),
                (11.00996, 76.98904),
                (11.00928, 76.99019),
                (11.0061, 76.99199),
                (11.0044, 76.99312),
                (11.00523, 76.99448),
                (11.00635, 76.9961),
                (11.0099, 76.99597),
                (11.01417, 76.99513),
                (11.01752, 76.99445),
                (11.02004, 76.99532),
                (11.01942, 76.99306),
                (11.01761, 76.99062),
                (11.01479, 76.98839),
                (11.01293, 76.98604),
                (11.01416, 76.98736),
                (11.01649, 76.98967),
                (11.01996, 76.99199),
                (11.0212, 76.99378),
                (11.02199, 76.99608),
                (11.02237, 76.99889),
                (11.02323, 77.00149),
                (11.0242, 77.0051),
                (11.02467, 77.00773),
                (11.02532, 77.01184),
                (11.02577, 77.01653),
                (11.0266, 77.02099),
                (11.02657, 77.02159),
                (11.02567, 77.01768),
                (11.02336, 77.01767),
                (11.02166, 77.01546),
                (11.01975, 77.01264),
                (11.01592, 77.01162),
                (11.01203, 77.01269),
                (11.0077, 77.01344),
                (11.00672, 77.01519),
                (11.00615, 77.01689),
                (11.00546, 77.01837),
                (11.00614, 77.02177),
                (11.00624, 77.02358),
                (11.00635, 77.0255),
                (11.00514, 77.02688),
                (11.00219, 77.02867),
                (11.00082, 77.03052),
                (11.00175, 77.03545),
                (11.00275, 77.03976),
                (11.0038, 77.04702),
                (11.00407, 77.04957),
            ],
        },
        "Route 2": {
            "color": "#9B59B6",
            "name": "Gandhipuram Hub → Saibaba Colony",
            "buses_allocated": 50,
            "stop_names": ["Gandhipuram Hub", "R.S. Puram", "Mettupalayam Road", "Saibaba Colony"],
            "stop_waypoint_indices": [0, 25, 42, 58],
            "waypoints": [
                (11.01617, 76.96929),
                (11.01618, 76.97003),
                (11.01624, 76.97023),
                (11.01646, 76.97015),
                (11.0166, 76.97),
                (11.01671, 76.96978),
                (11.01687, 76.96919),
                (11.01701, 76.96849),
                (11.01705, 76.968),
                (11.01705, 76.96695),
                (11.01708, 76.96637),
                (11.01711, 76.96577),
                (11.01712, 76.96564),
                (11.01712, 76.96543),
                (11.01713, 76.96483),
                (11.0171, 76.96378),
                (11.01707, 76.96303),
                (11.01807, 76.96251),
                (11.01926, 76.96237),
                (11.01922, 76.96214),
                (11.0191, 76.96149),
                (11.01899, 76.96093),
                (11.01888, 76.96033),
                (11.01886, 76.96029),
                (11.01871, 76.96026),
                (11.01792, 76.96025),
                (11.01691, 76.95981),
                (11.01683, 76.95857),
                (11.01681, 76.95808),
                (11.01679, 76.95655),
                (11.01683, 76.95561),
                (11.01674, 76.95461),
                (11.01663, 76.95343),
                (11.01679, 76.95312),
                (11.01712, 76.95306),
                (11.01738, 76.95319),
                (11.01763, 76.95338),
                (11.01796, 76.9536),
                (11.01842, 76.9535),
                (11.01994, 76.95294),
                (11.02082, 76.95259),
                (11.02179, 76.95209),
                (11.02176, 76.95224),
                (11.02149, 76.95237),
                (11.02086, 76.95267),
                (11.02043, 76.95286),
                (11.01998, 76.95304),
                (11.02038, 76.95277),
                (11.02102, 76.95251),
                (11.02233, 76.95182),
                (11.02278, 76.95171),
                (11.02322, 76.95157),
                (11.02312, 76.94986),
                (11.02308, 76.94893),
                (11.02302, 76.94764),
                (11.0229, 76.94632),
                (11.02398, 76.94615),
                (11.02393, 76.94555),
                (11.02432, 76.94479),
            ],
        },
        "Route 3": {
            "color": "#1ABC9C",
            "name": "Ukkadam Lake Depot ↔ Gandhipuram Hub",
            "buses_allocated": 50,
            "stop_names": ["Ukkadam Lake Depot", "Town Hall", "Ramanathapuram", "Gandhipuram Hub"],
            "stop_waypoint_indices": [0, 24, 45, 62],
            "waypoints": [
                (10.9881, 76.96188),
                (10.98877, 76.96166),
                (10.98869, 76.96134),
                (10.98804, 76.96138),
                (10.98787, 76.96157),
                (10.98794, 76.96253),
                (10.98801, 76.96334),
                (10.98834, 76.96474),
                (10.98852, 76.96536),
                (10.98873, 76.96568),
                (10.98898, 76.9652),
                (10.98916, 76.96485),
                (10.99084, 76.96543),
                (10.99201, 76.96563),
                (10.99376, 76.96596),
                (10.99396, 76.96427),
                (10.99393, 76.96302),
                (10.99379, 76.96199),
                (10.99359, 76.96098),
                (10.99335, 76.95976),
                (10.99417, 76.95821),
                (10.9946, 76.95679),
                (10.99601, 76.95543),
                (10.99717, 76.95614),
                (10.99724, 76.95758),
                (11.00042, 76.95824),
                (11.00111, 76.96138),
                (11.00131, 76.9621),
                (11.00145, 76.96249),
                (11.00164, 76.96258),
                (11.00168, 76.9628),
                (11.00158, 76.96298),
                (11.00192, 76.96428),
                (11.00211, 76.96508),
                (11.00239, 76.96673),
                (11.00227, 76.96666),
                (11.00171, 76.96614),
                (11.00142, 76.96609),
                (11.00123, 76.96564),
                (11.0013, 76.96594),
                (11.00153, 76.96611),
                (11.00216, 76.96617),
                (11.00223, 76.96546),
                (11.00408, 76.96638),
                (11.00616, 76.96656),
                (11.00713, 76.96666),
                (11.00784, 76.96674),
                (11.0081, 76.96687),
                (11.00867, 76.967),
                (11.01, 76.96717),
                (11.01168, 76.96737),
                (11.01308, 76.96752),
                (11.01505, 76.96783),
                (11.0158, 76.96802),
                (11.01669, 76.9683),
                (11.01706, 76.96884),
                (11.01686, 76.96959),
                (11.01672, 76.97008),
                (11.01655, 76.97028),
                (11.01635, 76.97043),
                (11.01623, 76.97039),
                (11.01621, 76.97026),
                (11.01617, 76.96929),
            ],
        },
    })

    # ─────────────────────────────────────────────
    # BATTERY SWAP STATIONS (Strategic Hubs)
    # ─────────────────────────────────────────────
    SWAP_STATIONS: List[dict] = field(default_factory=lambda: [
        {
            "id": 1,
            "name": "Ukkadam Solar Hub Station",
            "lat": 10.9883,
            "lon": 76.9619,
            "capacity_buses": 50,
            "desc": "Directly connected to Ukkadam Floating PV array (zero grid transmission losses)",
        },
        {
            "id": 2,
            "name": "Gandhipuram Central Hub",
            "lat": 11.0163,
            "lon": 76.9693,
            "capacity_buses": 40,
            "desc": "Major multi-modal TNSTC bus terminus interchange",
        },
        {
            "id": 3,
            "name": "Saibaba Colony Station",
            "lat": 11.0243,
            "lon": 76.9448,
            "capacity_buses": 25,
            "desc": "Mettupalayam road gateway swap facility",
        },
        {
            "id": 4,
            "name": "Ondipudur Depot Station",
            "lat": 11.0040,
            "lon": 77.0493,
            "capacity_buses": 35,
            "desc": "East corridor heavy transit depot facility",
        },
    ])
    SWAP_STATION_RADIUS_KM: float = 2.0    # qualifying proximity for swap scheduling
    CHARGER_BAYS_PER_STATION: int = 4     # active pack chargers at each hub
    CHARGE_RATE_KW: float = 150.0         # electrical input rate per charger
    CHARGER_EFFICIENCY: float = 0.92      # wall-to-pack charging efficiency

    # ─────────────────────────────────────────────
    # MUNICIPAL BUILDINGS (Captive power consumers)
    # ─────────────────────────────────────────────
    MUNICIPAL_BUILDINGS: List[dict] = field(default_factory=lambda: [
        {"name": "Coimbatore Corporation Office", "lat": 10.9965, "lon": 76.9585, "load_kw": 500},
        {"name": "Town Hall",                     "lat": 10.9968, "lon": 76.9556, "load_kw": 300},
        {"name": "Ukkadam Bus Depot",             "lat": 10.9883, "lon": 76.9619, "load_kw": 800},
    ])
    TOTAL_MUNICIPAL_LOAD_KW: float = 1_600.0   # Baseline municipal captive demand
    MUNICIPAL_OFFPEAK_LOAD_KW: float = 250.0   # Night-time captive demand
    MUNICIPAL_PEAK_START_HOUR: float = 7.0
    MUNICIPAL_PEAK_END_HOUR: float = 21.0

    # ─────────────────────────────────────────────
    # ECOLOGICAL PARAMETERS & WATER QUALITY
    # ─────────────────────────────────────────────
    DO_INITIAL: float = 5.5              # mg/L starting dissolved oxygen (eutrophic baseline)
    DO_MIN_OK: float = 4.0              # mg/L — below this triggers WARNING
    DO_AERATION_TRIGGER: float = 3.0    # mg/L — below this triggers EMERGENCY + AERATION
    DO_MAX: float = 9.0                 # mg/L — physical saturation ceiling

    # Water quality nominal baselines (display indicators)
    PH_BASE: float = 7.6                # standard lake pH

    # ── Dissolved-oxygen budget coefficients ───────────────────────────────
    # These live in src/models/ecology.py as EcologyParams. They are surfaced here so
    # that Config remains the single description of a scenario and so the dashboard
    # can build a sensitivity analysis without editing source. See the EcologyParams
    # docstring for the physical meaning of each and for which one is calibrated.
    DO_REAERATION_K_PER_HR: float = 0.028
    DO_GROSS_PHOTOSYNTHESIS_MG_L_PER_HR: float = 0.70
    DO_RESPIRATION_MG_L_PER_HR: float = 0.24
    DO_RESPIRATION_Q10: float = 1.08
    DO_RESPIRATION_REF_TEMP_C: float = 25.0
    #: CALIBRATED site parameter, not measured. The coverage-versus-oxygen result is
    #: proportional to it; the dashboard runs a sensitivity analysis over this value.
    DO_SHADE_EFFECTIVENESS: float = 2.1
    DO_ALGAE_SUPPRESSION_CREDIT: float = 0.11
    AERATION_RATE_MG_L_PER_HR: float = 0.22
    AERATION_RELEASE_MG_L: float = 4.2
    DO_SHADING_HOURS: Tuple[int, ...] = (10, 11, 12, 13, 14, 15)
    WATER_TEMP_BASE_C: float = 24.2
    WATER_TEMP_AMBIENT_GAIN: float = 0.55
    WATER_TEMP_AMBIENT_REF_C: float = 25.5
    WATER_TEMP_DIURNAL_AMPLITUDE_C: float = 1.6
    WATER_TEMP_DIURNAL_PEAK_HOUR: float = 15.0
    PV_COOLING_DELTA_C: float = 1.40
    PV_COOLING_NIGHT_FRACTION: float = 0.11
    PH_PHOTOSYNTHETIC_RISE: float = 0.65
    PH_COVERAGE_PENALTY: float = 0.15
    TURBIDITY_BASE_MODEL_NTU: float = 25.0
    TURBIDITY_COVERAGE_REDUCTION_NTU: float = 4.5

    # ─────────────────────────────────────────────
    # ECONOMICS & TARIFFS
    # ─────────────────────────────────────────────
    GRID_TARIFF_PER_KWH: float = 8.0           # ₹/kWh (TANGEDCO commercial rate)
    OA_SURCHARGE_PER_KWH: float = 0.54         # ₹/kWh avoided via captive model
    DIESEL_COST_PER_LITRE: float = 100.0       # ₹/L
    DIESEL_KM_PER_LITRE: float = 3.5           # TNSTC diesel bus fuel economy
    DIESEL_KM_PER_BUS_PER_DAY: float = 220.0   # TNSTC operational estimate
    CO2_GRID_KG_PER_KWH: float = 0.82          # kg CO₂/kWh (CEA India baseline)
    CO2_DIESEL_KG_PER_LITRE: float = 2.68      # kg CO₂/litre diesel

    # Financial CAPEX / OPEX
    CAPEX_PER_MWP_CR: float = 6.5             # ₹ Crore / MWp for floating PV
    OPEX_PER_MWP_CR_YEAR: float = 0.15        # ₹ Crore / MWp / year O&M
    BATTERY_PACK_COST_LAKH: float = 25.0      # ₹ Lakh per 300 kWh swap pack
    PACK_REPLACEMENT_YEARS: int = 8           # whole-fleet repack interval
    STATION_CAPEX_LAKH: float = 120.0         # ₹ Lakh civil + electrical per swap hub
    SYSTEM_LIFETIME_YEARS: int = 25
    DISCOUNT_RATE: float = 0.08                # 8%

    # ─────────────────────────────────────────────
    # MAP VIEWPORT
    # ─────────────────────────────────────────────
    # There is deliberately no hand-picked map centre. The initial view is derived
    # from the actual geometry (lake polygon, array polygon, swap stations and every
    # route waypoint) by :meth:`map_view`, so no bus, corridor or asset can ever sit
    # outside the opening viewport. The previous hard-coded centre was Coimbatore's
    # city centre, which is ~4 km north-east of Ukkadam Lake and cropped every route
    # east of ~77.02 deg E.
    MAP_TILES: str = "OpenStreetMap"
    #: Padding, in screen pixels, left around the fitted geometry by Leaflet.
    MAP_VIEW_PADDING_PX: int = 28
    #: Zoom used by the map's "focus lake" control, which frames the array itself.
    MAP_LAKE_ZOOM: int = 15
    #: Zoom band the lake close-up is clamped to.
    MAP_ZOOM_MIN: int = 10
    MAP_ZOOM_MAX: int = 18

    # ─────────────────────────────────────────────
    # MONTHLY CLIMATOLOGY (Coimbatore IMD Normals)
    # ─────────────────────────────────────────────
    MONTHLY_TEMP_C: Dict[int, float] = field(default_factory=lambda: {
        1: 25.5, 2: 27.0, 3: 30.0, 4: 32.5, 5: 33.0, 6: 30.0,
        7: 29.0, 8: 29.0, 9: 29.5, 10: 28.0, 11: 26.5, 12: 25.0,
    })

    # ─────────────────────────────────────────────
    # DERIVED QUANTITIES AND MODEL FACTORIES
    # ─────────────────────────────────────────────

    def _map_geometry(self) -> List[Tuple[float, float]]:
        """Every ``(lat, lon)`` point the map must keep inside its opening viewport.

        Collects the lake outline, the floating-array outline, every battery-swap
        station and every route waypoint. Derived rather than configured so that
        editing a route or moving a station can never push geometry off-screen.
        """
        points: List[Tuple[float, float]] = []
        points.extend(self.LAKE_POLYGON)
        points.extend(self.SOLAR_ARRAY_POLYGON)
        points.extend((s["lat"], s["lon"]) for s in self.SWAP_STATIONS)
        for route in self.ROUTES.values():
            points.extend(route["waypoints"])
        return points

    @staticmethod
    def _bounds_of(points: Sequence[Tuple[float, float]]) -> Tuple[
        Tuple[float, float], Tuple[float, float]
    ]:
        """Bounding box of ``points`` as Leaflet's ``(south, west), (north, east)``."""
        if not points:
            raise ValueError("_bounds_of requires at least one point")
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        return (min(lats), min(lons)), (max(lats), max(lons))

    def lake_volume_m3(self) -> float:
        """Lake volume from surface area times mean depth, in cubic metres.

        Uses :attr:`LAKE_AREA_M2` (1.295 km2) and :attr:`LAKE_DEPTH_M` (5.82 m), both
        matching the published figures for Ukkadam Periyakulam, giving ~7.54 million m3.

        Note that the same source quotes a volume of only ~1.98 million m3, which is
        inconsistent with area x mean depth by a factor of about 3.8. That source is
        internally inconsistent, so this project uses the area and the mean depth --
        the two quantities it needs anyway -- and does not rely on a volume figure.
        Reported for transparency; the oxygen budget is areal and does not consume it.
        """
        return self.LAKE_AREA_M2 * self.LAKE_DEPTH_M

    #: Base raster tile providers, keyed by the name accepted in :attr:`MAP_TILES`.
    #: Chosen so the map needs no API key, no account and no watermark.
    TILE_PROVIDERS: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "OpenStreetMap": {
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "&copy; OpenStreetMap contributors",
            "max_zoom": "19",
        },
    })

    def tile_provider(self) -> Dict[str, str]:
        """Resolved tile provider for :attr:`MAP_TILES`.

        Raises ``ValueError`` for an unknown name rather than silently falling back, so
        a typo cannot quietly ship the wrong basemap.
        """
        try:
            provider = self.TILE_PROVIDERS[self.MAP_TILES]
        except KeyError:
            raise ValueError(
                f"unknown MAP_TILES {self.MAP_TILES!r}; "
                f"expected one of {sorted(self.TILE_PROVIDERS)}"
            ) from None
        return provider

    def map_bounds(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Opening map bounds as ``((south, west), (north, east))``.

        Covers the lake outline, the floating array, every swap station and every route
        waypoint, so nothing can sit outside the opening viewport. The **zoom is
        deliberately not computed here**: it depends on the rendered container size,
        which only the browser knows. Leaflet derives it from these bounds via
        ``fitBounds``, which is why this project no longer carries a hard-coded centre
        and zoom. The previous pair was Coimbatore's city centre at zoom 13, about
        4 km north-east of Ukkadam Lake, which cropped the lake and every corridor
        east of roughly 77.02 deg E.
        """
        return self._bounds_of(self._map_geometry())

    def map_center(self) -> Tuple[float, float]:
        """Midpoint of :meth:`map_bounds`, for tests and reporting.

        This is the arithmetic centre of the geometry, not a hand-chosen point; it is
        *not* what positions the map, because Leaflet centres on the projected bounds.
        """
        (south, west), (north, east) = self.map_bounds()
        return (south + north) / 2.0, (west + east) / 2.0

    def lake_view(self) -> Tuple[float, float, int]:
        """Close-up view of the floating array: ``(centre_lat, centre_lon, zoom)``.

        Centred on the mean solar-array polygon vertex and clamped to
        :attr:`MAP_ZOOM_MIN`/:attr:`MAP_ZOOM_MAX`. Used by the map's "focus lake"
        control, because the fitted opening view necessarily spans the whole ~17 km
        network and therefore renders the lake quite small.
        """
        poly = self.SOLAR_ARRAY_POLYGON or self.LAKE_POLYGON
        lat = sum(p[0] for p in poly) / len(poly)
        lon = sum(p[1] for p in poly) / len(poly)
        zoom = max(self.MAP_ZOOM_MIN, min(self.MAP_ZOOM_MAX, self.MAP_LAKE_ZOOM))
        return lat, lon, zoom

    def capacity_mwp(self, coverage_pct: Optional[float] = None) -> float:
        """Installed capacity in MWp for a given lake-surface coverage."""
        pct = self.DEFAULT_COVERAGE_PCT if coverage_pct is None else coverage_pct
        return (self.LAKE_AREA_M2 * (pct / 100.0)) / self.PANEL_AREA_M2_PER_MWP

    def annual_generation_mwh(self, coverage_pct: Optional[float] = None) -> float:
        """Analytic planning estimate of annual generation, in MWh.

        This is the *specific-yield* method: installed capacity multiplied by a
        measured plane-of-array yield for Coimbatore and by the floating-versus-ground
        cooling premium. It is fast, transparent, and exactly proportional to coverage,
        which makes it useful for scenario ranking.

        It is **not** the authoritative annual figure, because it cannot represent
        cloud seasonality, the diurnal load shape, or curtailment. The dashboard
        reports the hourly integrated result from
        :meth:`~src.simulation.engine.SimulationEngine.run_annual_simulation` as the
        headline and shows this estimate alongside it with the difference explained.
        """
        cap_mwp = self.capacity_mwp(coverage_pct)
        annual_kwh = cap_mwp * 1_000.0 * self.ANNUAL_YIELD_KWH_PER_KWP * self.COOLING_BONUS_FACTOR
        return annual_kwh / 1_000.0   # MWh

    def max_generation_kw(self, coverage_pct: Optional[float] = None) -> float:
        """Peak generation capacity in kW, scaled to a coverage percentage."""
        pct = self.DEFAULT_COVERAGE_PCT if coverage_pct is None else coverage_pct
        return self.MAX_GENERATION_KW * (self.capacity_mwp(pct) / self.capacity_mwp(15.0))

    def municipal_load_kw(self, hour: float) -> float:
        """Municipal captive demand in kW at a given hour.

        The three named buildings together draw 1,600 kW, which is exactly
        :attr:`TOTAL_MUNICIPAL_LOAD_KW`; the off-peak figure is a reduced
        administrative load. Previously the engine hardcoded the comparison
        ``7.0 <= hour <= 21.0`` inline, which silently included 21:30 and excluded
        06:30, so the daytime window was half an hour out at both ends.
        """
        if self.MUNICIPAL_PEAK_START_HOUR <= hour < self.MUNICIPAL_PEAK_END_HOUR:
            return self.TOTAL_MUNICIPAL_LOAD_KW
        return self.MUNICIPAL_OFFPEAK_LOAD_KW

    def ecology_params(self) -> "EcologyParams":
        """Build :class:`~src.models.ecology.EcologyParams` from this configuration."""
        from src.models.ecology import EcologyParams

        return EcologyParams(
            reaeration_k_per_hr=self.DO_REAERATION_K_PER_HR,
            gross_photosynthesis_mg_l_per_hr=self.DO_GROSS_PHOTOSYNTHESIS_MG_L_PER_HR,
            respiration_mg_l_per_hr=self.DO_RESPIRATION_MG_L_PER_HR,
            respiration_q10=self.DO_RESPIRATION_Q10,
            respiration_ref_temp_c=self.DO_RESPIRATION_REF_TEMP_C,
            shade_effectiveness=self.DO_SHADE_EFFECTIVENESS,
            algae_suppression_credit=self.DO_ALGAE_SUPPRESSION_CREDIT,
            aeration_rate_mg_l_per_hr=self.AERATION_RATE_MG_L_PER_HR,
            aeration_on_below_mg_l=self.DO_AERATION_TRIGGER,
            aeration_off_above_mg_l=self.AERATION_RELEASE_MG_L,
            do_max_mg_l=self.DO_MAX,
            shading_hours=tuple(self.DO_SHADING_HOURS),
            water_temp_base_c=self.WATER_TEMP_BASE_C,
            water_temp_ambient_gain=self.WATER_TEMP_AMBIENT_GAIN,
            water_temp_ambient_ref_c=self.WATER_TEMP_AMBIENT_REF_C,
            water_temp_diurnal_amplitude_c=self.WATER_TEMP_DIURNAL_AMPLITUDE_C,
            water_temp_diurnal_peak_hour=self.WATER_TEMP_DIURNAL_PEAK_HOUR,
            pv_cooling_delta_c=self.PV_COOLING_DELTA_C,
            pv_cooling_night_fraction=self.PV_COOLING_NIGHT_FRACTION,
            ph_base=self.PH_BASE,
            ph_photosynthetic_rise=self.PH_PHOTOSYNTHETIC_RISE,
            ph_coverage_penalty=self.PH_COVERAGE_PENALTY,
            turbidity_base_ntu=self.TURBIDITY_BASE_MODEL_NTU,
            turbidity_coverage_reduction_ntu=self.TURBIDITY_COVERAGE_REDUCTION_NTU,
        )

    def scenario_signature(self) -> Dict[str, Any]:
        """Return the scalar settings that define a scenario, for hashing and manifests."""
        return {
            "coverage_pct": self.DEFAULT_COVERAGE_PCT,
            "num_buses": self.NUM_BUSES,
            "battery_capacity_kwh": self.BATTERY_CAPACITY_KWH,
            "daily_consumption_kwh": self.DAILY_CONSUMPTION_KWH,
            "service_window_h": [self.SERVICE_START_HOUR, self.SERVICE_END_HOUR],
            "traffic_factor": self.TRAFFIC_INTENSITY_FACTOR,
            "cloud_multiplier": self.CLOUD_COVER_MULTIPLIER,
            "charger_bays": self.CHARGER_BAYS_PER_STATION,
            "charge_rate_kw": self.CHARGE_RATE_KW,
            "charger_efficiency": self.CHARGER_EFFICIENCY,
            "shade_effectiveness": self.DO_SHADE_EFFECTIVENESS,
            "tariff_inr_per_kwh": self.GRID_TARIFF_PER_KWH,
        }
