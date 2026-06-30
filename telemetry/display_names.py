from __future__ import annotations

import re


CAR_NAMES = {
    "porsche_911_gt3": "Porsche 911 GT3",
    "porsche_911_gt3_r": "Porsche 911 GT3 R",
    "porsche_991_gt3_r": "Porsche 991 GT3 R",
    "porsche_992_gt3_r": "Porsche 992 GT3 R",
    "bmw_m4_gt3": "BMW M4 GT3",
    "bmw_m6_gt3": "BMW M6 GT3",
    "mercedes_amg_gt3": "Mercedes-AMG GT3",
    "mercedes_amg_gt3_evo": "Mercedes-AMG GT3 Evo",
    "audi_r8_lms": "Audi R8 LMS",
    "audi_r8_lms_evo": "Audi R8 LMS Evo",
    "audi_r8_lms_evo_ii": "Audi R8 LMS Evo II",
    "ferrari_488_gt3": "Ferrari 488 GT3",
    "ferrari_488_gt3_evo": "Ferrari 488 GT3 Evo",
    "ferrari_296_gt3": "Ferrari 296 GT3",
    "lamborghini_huracan_gt3": "Lamborghini Huracan GT3",
    "lamborghini_huracan_gt3_evo": "Lamborghini Huracan GT3 Evo",
    "lamborghini_huracan_gt3_evo2": "Lamborghini Huracan GT3 Evo2",
    "mclaren_720s_gt3": "McLaren 720S GT3",
    "mclaren_720s_gt3_evo": "McLaren 720S GT3 Evo",
    "aston_martin_v8_vantage_gt3": "Aston Martin V8 Vantage GT3",
    "bentley_continental_gt3": "Bentley Continental GT3",
    "nissan_gt_r_nismo_gt3": "Nissan GT-R Nismo GT3",
    "honda_nsx_gt3": "Honda NSX GT3",
    "honda_nsx_gt3_evo": "Honda NSX GT3 Evo",
    "lexus_rc_f_gt3": "Lexus RC F GT3",
    "ford_mustang_gt3": "Ford Mustang GT3",
    "alpine_a110_gt4": "Alpine A110 GT4",
    "amr_v8_vantage_gt4": "Aston Martin V8 Vantage GT4",
    "f1_2018_mercedes": "Mercedes-AMG Petronas",
    "f1_2018_ferrari": "Scuderia Ferrari",
    "f1_2018_red_bull": "Red Bull Racing",
    "f1_2018_mclaren": "McLaren",
    "f1_2018_williams": "Williams",
    "f1_2018_renault": "Renault",
    "f1_2018_toro_rosso": "Toro Rosso",
    "f1_2018_haas": "Haas",
    "f1_2018_force_india": "Force India",
    "f1_2018_sauber": "Sauber",
    "f1_2021_mercedes": "Mercedes-AMG Petronas",
    "f1_2021_ferrari": "Scuderia Ferrari",
    "f1_2021_red_bull": "Red Bull Racing",
    "f1_2021_mclaren": "McLaren",
    "f1_2021_alpine": "Alpine",
    "f1_2021_alpha_tauri": "AlphaTauri",
    "f1_2021_aston_martin": "Aston Martin",
    "f1_2021_williams": "Williams",
    "f1_2021_alfa_romeo": "Alfa Romeo",
    "f1_2021_haas": "Haas",
}


TRACK_NAMES = {
    "ks_silverstone": "Silverstone",
    "silverstone": "Silverstone",
    "monza": "Monza",
    "ks_monza66": "Monza 1966",
    "spa": "Spa-Francorchamps",
    "spa_francorchamps": "Spa-Francorchamps",
    "nurburgring": "Nurburgring",
    "nurburgring_24h": "Nurburgring 24H",
    "nurburgringgp": "Nurburgring GP",
    "brands_hatch": "Brands Hatch",
    "zolder": "Zolder",
    "misano": "Misano",
    "paul_ricard": "Paul Ricard",
    "hungaroring": "Hungaroring",
    "zandvoort": "Zandvoort",
    "barcelona": "Barcelona-Catalunya",
    "circuit_de_barcelona_catalunya": "Barcelona-Catalunya",
    "mount_panorama": "Mount Panorama",
    "bathurst": "Mount Panorama",
    "suzuka": "Suzuka",
    "laguna_seca": "Laguna Seca",
    "kyalami": "Kyalami",
    "imola": "Imola",
    "oulton_park": "Oulton Park",
    "donington": "Donington Park",
    "snetterton": "Snetterton",
    "cota": "Circuit of the Americas",
    "indianapolis": "Indianapolis",
    "watkins_glen": "Watkins Glen",
    "valencia": "Valencia",
    "red_bull_ring": "Red Bull Ring",
    "f1_2018_melbourne": "Melbourne",
    "f1_2018_bahrain": "Bahrain",
    "f1_2018_china": "Shanghai",
    "f1_2018_azerbaijan": "Baku",
    "f1_2018_spain": "Barcelona-Catalunya",
    "f1_2018_monaco": "Monaco",
    "f1_2018_canada": "Circuit Gilles Villeneuve",
    "f1_2018_france": "Paul Ricard",
    "f1_2018_austria": "Red Bull Ring",
    "f1_2018_britain": "Silverstone",
    "f1_2018_germany": "Hockenheimring",
    "f1_2018_hungary": "Hungaroring",
    "f1_2018_belgium": "Spa-Francorchamps",
    "f1_2018_italy": "Monza",
    "f1_2018_singapore": "Marina Bay",
    "f1_2018_russia": "Sochi",
    "f1_2018_japan": "Suzuka",
    "f1_2018_usa": "Circuit of the Americas",
    "f1_2018_mexico": "Mexico City",
    "f1_2018_brazil": "Interlagos",
    "f1_2018_abudhabi": "Yas Marina",
    "f1_2021_bahrain": "Bahrain",
    "f1_2021_italy": "Imola",
    "f1_2021_portugal": "Portimao",
    "f1_2021_spain": "Barcelona-Catalunya",
    "f1_2021_monaco": "Monaco",
    "f1_2021_azerbaijan": "Baku",
    "f1_2021_canada": "Circuit Gilles Villeneuve",
    "f1_2021_france": "Paul Ricard",
    "f1_2021_austria": "Red Bull Ring",
    "f1_2021_britain": "Silverstone",
    "f1_2021_hungary": "Hungaroring",
    "f1_2021_belgium": "Spa-Francorchamps",
    "f1_2021_netherlands": "Zandvoort",
    "f1_2021_russia": "Sochi",
    "f1_2021_singapore": "Marina Bay",
    "f1_2021_japan": "Suzuka",
    "f1_2021_usa": "Circuit of the Americas",
    "f1_2021_mexico": "Mexico City",
    "f1_2021_brazil": "Interlagos",
    "f1_2021_australia": "Melbourne",
    "f1_2021_saudi_arabia": "Jeddah",
    "f1_2021_abudhabi": "Yas Marina",
}


AC_PREFIXES = ("ks_", "acc_", "ac_")
ACRONYMS = {"gt", "gt3", "gt4", "gtr", "amg", "bmw", "f1", "usa", "cota", "nsx", "lms", "rc", "ii"}


def display_car_name(name: str | None) -> str:
    return display_name(name, CAR_NAMES)


def display_track_name(name: str | None) -> str:
    return display_name(name, TRACK_NAMES)


def display_name(name: str | None, catalog: dict[str, str]) -> str:
    if not name:
        return "--"
    key = normalize_key(name)
    if key in catalog:
        return catalog[key]
    return readable_identifier(name)


def normalize_key(value: str) -> str:
    text = value.strip().replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text)
    return text.lower().strip("_")


def readable_identifier(value: str) -> str:
    text = normalize_key(value)
    for prefix in AC_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    words = [word for word in re.split(r"[_\s]+", text) if word]
    return " ".join(format_word(word) for word in words) or "--"


def format_word(word: str) -> str:
    lower = word.lower()
    if lower in ACRONYMS or any(char.isdigit() for char in lower):
        return lower.upper()
    if lower in {"huracan"}:
        return "Huracan"
    return lower.capitalize()
