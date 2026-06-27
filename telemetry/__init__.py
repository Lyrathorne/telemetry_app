from telemetry.assetto_corsa import AssettoCorsaTelemetrySource
from telemetry.assetto_corsa_competizione import AccTelemetrySource
from telemetry.demo import DemoTelemetrySource
from telemetry.f1_2018 import F12018TelemetrySource


SOURCE_TYPES = {
    "demo": DemoTelemetrySource,
    "f1_2018": F12018TelemetrySource,
    "assetto_corsa": AssettoCorsaTelemetrySource,
    "assetto_corsa_competizione": AccTelemetrySource,
}


SOURCE_LABELS = {
    "demo": "Demo",
    "f1_2018": "F1 2018",
    "assetto_corsa": "Assetto Corsa",
    "assetto_corsa_competizione": "Assetto Corsa Competizione",
}
