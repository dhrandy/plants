"""Starter library of common houseplants.

Each entry pre-fills a new plant. The check interval is a conservative
starting point for *checking the soil*, not a watering schedule; every value
stays editable. Care notes are summarized from the linked North Carolina
Extension Gardener Plant Toolbox pages.
"""

SOURCE = "North Carolina Extension Gardener Plant Toolbox"

LIBRARY = [
    {
        "key": "pothos",
        "name": "Pothos",
        "species": "Epipremnum aureum",
        "light": "Bright, indirect light; tolerates low light",
        "water_days": 7,
        "fertilize_days": 30,
        "care": "Let the potting mix dry out between waterings. Overwatering can cause root rot.",
        "source": "https://plants.ces.ncsu.edu/plants/epipremnum-aureum/",
    },
    {
        "key": "snake-plant",
        "name": "Snake plant",
        "species": "Dracaena trifasciata",
        "light": "Some direct sun; tolerates very low light",
        "water_days": 14,
        "fertilize_days": 60,
        "care": "Well-drained soil and careful watering. Do not overwater; the roots rot.",
        "source": "https://plants.ces.ncsu.edu/plants/dracaena-trifasciata/",
    },
    {
        "key": "zz-plant",
        "name": "ZZ plant",
        "species": "Zamioculcas zamiifolia",
        "light": "Low to medium light",
        "water_days": 14,
        "fertilize_days": 60,
        "care": "Drought tolerant rhizomes. Needs good drainage; let it dry out.",
        "source": "https://plants.ces.ncsu.edu/plants/zamioculcas-zamiifolia/",
    },
    {
        "key": "peace-lily",
        "name": "Peace lily",
        "species": "Spathiphyllum",
        "light": "Low to medium light, no direct sun",
        "water_days": 5,
        "fertilize_days": 45,
        "care": "Likes evenly moist soil and warm rooms. Keep away from cold drafts.",
        "source": "https://plants.ces.ncsu.edu/plants/spathiphyllum/",
    },
    {
        "key": "spider-plant",
        "name": "Spider plant",
        "species": "Chlorophytum comosum",
        "light": "Medium light, no direct sun",
        "water_days": 7,
        "fertilize_days": 30,
        "care": "Grows best in moist soil but tolerates some dryness.",
        "source": "https://plants.ces.ncsu.edu/plants/chlorophytum-comosum/",
    },
    {
        "key": "monstera",
        "name": "Monstera",
        "species": "Monstera deliciosa",
        "light": "Moderate brightness, no direct sun",
        "water_days": 7,
        "fertilize_days": 30,
        "care": "Water thoroughly, then let the top of the soil dry before watering again. Likes humidity.",
        "source": "https://plants.ces.ncsu.edu/plants/monstera-deliciosa/",
    },
    {
        "key": "fiddle-leaf-fig",
        "name": "Fiddle-leaf fig",
        "species": "Ficus lyrata",
        "light": "Bright, indirect light",
        "water_days": 7,
        "fertilize_days": 30,
        "care": "Sensitive to overwatering. Moist but well-drained soil.",
        "source": "https://plants.ces.ncsu.edu/plants/ficus-lyrata/",
    },
    {
        "key": "boston-fern",
        "name": "Boston fern",
        "species": "Nephrolepis exaltata",
        "light": "Bright, indirect light",
        "water_days": 3,
        "fertilize_days": 30,
        "mist_days": 3,
        "care": "Needs high humidity and soil that never fully dries out.",
        "source": "https://plants.ces.ncsu.edu/plants/nephrolepis-exaltata/",
    },
    {
        "key": "aloe",
        "name": "Aloe vera",
        "species": "Aloe vera",
        "light": "Full sun to partial shade",
        "water_days": 14,
        "fertilize_days": 90,
        "care": "Let the soil dry completely between waterings. Overwatering rots the roots.",
        "source": "https://plants.ces.ncsu.edu/plants/aloe-vera/",
    },
    {
        "key": "rubber-plant",
        "name": "Rubber plant",
        "species": "Ficus elastica",
        "light": "Bright, indirect light or partial shade",
        "water_days": 10,
        "fertilize_days": 30,
        "care": "Prefers soil on the dry side. Overwatering and cold drafts drop leaves.",
        "source": "https://plants.ces.ncsu.edu/plants/ficus-elastica/",
    },
    {
        "key": "heartleaf-philodendron",
        "name": "Heartleaf philodendron",
        "species": "Philodendron hederaceum",
        "light": "Medium light; tolerates low light",
        "water_days": 7,
        "fertilize_days": 30,
        "mist_days": 7,
        "care": "Keep soil slightly moist and water less in winter. Appreciates misting.",
        "source": "https://plants.ces.ncsu.edu/plants/philodendron-hederaceum/",
    },
    {
        "key": "jade",
        "name": "Jade plant",
        "species": "Crassula ovata",
        "light": "Bright light with some afternoon shade",
        "water_days": 14,
        "fertilize_days": 90,
        "care": "Water in moderation. Overwatering causes leaf drop and root rot.",
        "source": "https://plants.ces.ncsu.edu/plants/crassula-ovata/",
    },
]


# Plant groups for species that aren't in the starter library. These are broad,
# conservative starting points for checking the soil, keyed by genus or family
# as Pl@ntNet reports them. They're a head start, not a rule; every value stays
# editable and nothing changes a schedule after the plant is added.
SYNONYMS = {
    "sansevieria trifasciata": "dracaena trifasciata",
    "sansevieria": "dracaena",
    "spathiphyllum wallisii": "spathiphyllum",
}

GROUPS = [
    # (match on, names, label, water, fertilize, mist)
    ("family", {"cactaceae"}, "cacti", 21, 90, None),
    ("family", {"crassulaceae"}, "succulents (stonecrop family)", 14, 90, None),
    ("genus", {"aloe", "haworthia", "haworthiopsis", "gasteria"}, "aloes and relatives", 14, 90, None),
    ("genus", {"dracaena", "sansevieria", "zamioculcas"}, "dracaenas and snake plants", 14, 60, None),
    ("genus", {"ficus"}, "figs", 7, 30, None),
    ("genus", {"peperomia"}, "peperomias", 10, 30, None),
    ("genus", {"hoya"}, "hoyas", 10, 30, None),
    ("family", {"marantaceae"}, "prayer plants and calatheas", 5, 30, 5),
    ("family", {"orchidaceae"}, "orchids", 7, 30, None),
    ("family", {"bromeliaceae"}, "bromeliads", 7, 60, None),
    ("family", {"begoniaceae"}, "begonias", 5, 30, None),
    ("family", {"araceae"}, "aroids (philodendron, anthurium and relatives)", 7, 30, None),
    ("family", {"nephrolepidaceae", "pteridaceae", "aspleniaceae", "polypodiaceae", "davalliaceae", "dryopteridaceae"},
     "ferns", 4, 30, 3),
    ("family", {"commelinaceae"}, "spiderworts (tradescantia)", 7, 30, None),
]


def _norm(value: str) -> str:
    value = " ".join((value or "").lower().split())
    return SYNONYMS.get(value, value)


def care_suggestion(species: str, genus: str = "", family: str = "") -> dict | None:
    """Suggested starting care for a species name, or None when there's nothing sensible to say.

    Order: exact starter-library species, then a library plant of the same genus,
    then a broad plant group by genus or family.
    """
    sp = _norm(species)
    gen = _norm(genus) or (sp.split(" ")[0] if sp else "")
    fam = _norm(family)
    if not sp and not gen and not fam:
        return None

    def from_library(entry, match):
        return {
            "match": match,
            "basis": f"{entry['name']} in the starter library",
            "library_key": entry["key"],
            "water_days": entry["water_days"],
            "fertilize_days": entry.get("fertilize_days"),
            "mist_days": entry.get("mist_days"),
            "light": entry["light"],
            "care": entry["care"],
            "source": entry["source"],
        }

    for entry in LIBRARY:
        if sp and _norm(entry["species"]) == sp:
            return from_library(entry, "species")
    for entry in LIBRARY:
        if gen and _norm(entry["species"]).split(" ")[0] == gen:
            return from_library(entry, "genus")
    for field, names, label, water, fert, mist in GROUPS:
        value = gen if field == "genus" else fam
        if value and value in names:
            return {
                "match": "group",
                "basis": f"general guide for {label}",
                "library_key": None,
                "water_days": water,
                "fertilize_days": fert,
                "mist_days": mist,
                "light": "",
                "care": "",
                "source": "",
            }
    return None
