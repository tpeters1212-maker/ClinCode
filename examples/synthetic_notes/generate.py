"""Generate fabricated pediatric foot and ankle notes for demos and tests.

Every value is random. The stratum mix roughly follows the real cohort:
mostly unspecified AFO, some casting and SMO, few explicit nighttime AFO.
"""

import csv
import random
from pathlib import Path

rng = random.Random(20260928)

HISTORY = [
    "{age} yo {sex} with idiopathic toe walking.",
    "{age} year old {sex} seen for bilateral heel cord tightness.",
    "{age} yo {sex} with cerebral palsy, spastic diplegia, and equinus.",
    "{age} y.o. {sex} referred for toe walking since starting to walk.",
    "Follow up for {age} yo {sex} with Achilles contracture.",
]
BRACE = {
    "explicit_nighttime": [
        "Wearing nighttime AFOs {n} nights per week.",
        "Using night splints most nights; family reports good tolerance.",
        "Wears AFO at night only, about {n} nights per week.",
    ],
    "serial_casting": [
        "Completed serial casting x{c} with improvement in dorsiflexion.",
        "Discussed serial casting; family agrees to start next week.",
        "Serial casting #{c} applied today in maximal dorsiflexion.",
    ],
    "smo": [
        "Currently wearing SMOs daily for school.",
        "Wears bilateral SMOs; no skin issues.",
    ],
    "afo_unspecified": [
        "Wearing AFOs, schedule not specified.",
        "Has bilateral AFOs from outside provider.",
        "AFOs fit well. Parent reports he wears them most of the time.",
        "Outgrowing AFOs; new pair ordered.",
        "Not wearing the AFOs much due to discomfort.",
    ],
    "other": [
        "No bracing at this time.",
        "Home stretching program only.",
        "",
    ],
}
MIX = [("afo_unspecified", 0.55), ("serial_casting", 0.14), ("smo", 0.09), ("explicit_nighttime", 0.05), ("other", 0.17)]
ROM = [
    "Ankle dorsiflexion with knee extended is {a} degrees bilaterally; with knee flexed {b} degrees.",
    "DF knee extended {a}° right, {a2}° left. Knee flexed {b} degrees.",
    "Right ankle lacks {l} degrees to neutral with the knee extended.",
    "Dorsiflexion to neutral with knee straight, {b} degrees with knee bent.",
    "Ankle ROM full.",
]
GAIT = [
    "Gait: constant toe walking, no heel strike.",
    "Gait: intermittent toe walking, able to heel strike when prompted.",
    "Heel-toe gait today. Improved compared with last visit.",
    "Toe walks most of the time. Stable from prior.",
    "Walks with heel strike, rare toe walking.",
]
ALIGN = ["", "", "Mild hindfoot valgus, flexible.", "Pes planus, corrects on toe rise.", "Standing radiographs reviewed: calcaneal pitch within normal limits."]
PLAN = [
    "Plan: continue current bracing, return in 3 months.",
    "Plan: continue stretching. Consider serial casting if no improvement.",
    "Plan: discussed Achilles lengthening versus gastrocnemius recession; family will consider.",
    "Plan: new AFOs ordered. Follow up in 4 months.",
    "Plan: surgery scheduled for gastrocnemius recession.",
    "Plan: follow up as needed.",
]


def stratum() -> str:
    x, acc = rng.random(), 0.0
    for name, p in MIX:
        acc += p
        if x < acc:
            return name
    return "other"


def note() -> str:
    fill = dict(age=rng.randint(2, 14), sex=rng.choice(["male", "female"]), n=rng.randint(3, 7), c=rng.randint(1, 4),
                a=rng.choice([-10, -5, 0, 5, 10, 15]), a2=rng.choice([-5, 0, 5, 10]), b=rng.choice([5, 10, 15, 20]),
                l=rng.choice([5, 10, 15]))
    parts = [
        "HISTORY: " + rng.choice(HISTORY),
        rng.choice(BRACE[stratum()]),
        "EXAM: " + rng.choice(ROM) + " " + rng.choice(ALIGN),
        rng.choice(GAIT),
        rng.choice(PLAN),
    ]
    return "\n".join(p for p in parts if p.strip()).format(**fill)


def main(n: int = 240) -> None:
    out = Path(__file__).with_name("demo_notes.csv")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["note_id", "patient_id", "note_date", "note_type", "text"])
        for i in range(n):
            pid = f"DP{rng.randint(1, 90):03d}"
            date = f"202{rng.randint(2, 5)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            w.writerow([f"DEMO-{i + 1:04d}", pid, date, "Clinic note", note()])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
