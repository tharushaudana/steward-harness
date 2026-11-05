"""A small, fictional knowledge base (Northwind Bikes) for tests, demos and agents. No credentials needed.

Structure: 2 domains, 4 topics, 9 sections (one management-approved price list that supersedes an outdated line of
the old price page), values for 3 bike models, a delivery fee entity and one calculator (order total for N bikes).
"""

from __future__ import annotations

from steward.knowledge.model import (
    CalcInput,
    Calculator,
    Entity,
    KBMeta,
    KnowledgeSnapshot,
    Section,
    SupersedeMark,
    TreeNode,
    UserInput,
    Value,
)


def tiny_kb(version: str = "nw-1") -> KnowledgeSnapshot:
    nodes = (
        TreeNode("ROOT", None, "root", "Northwind Bikes", "A fictional bicycle shop"),
        TreeNode("D1", "ROOT", "domain", "Bikes", "Bike models, prices, sizes", 1),
        TreeNode("D2", "ROOT", "domain", "Shop and service", "Workshop, delivery, returns, contact", 2),
        TreeNode("T1.1", "D1", "topic", "Models and specifications", "Frame sizes, weight, gears of each bike", 1),
        TreeNode("T1.2", "D1", "topic", "Prices", "Bike prices and delivery fees", 2),
        TreeNode("T2.1", "D2", "topic", "Workshop", "Repairs, servicing, tune-ups", 1),
        TreeNode("T2.2", "D2", "topic", "Orders and returns", "Delivery, returns, warranty, contact", 2),
    )
    s = Section
    sections = (
        s("S1", "Trail 500 mountain bike", "Trail 500 mountain bike\nFrame sizes: S, M, L, XL. Weight: 13.2 kg. 12 gears.\n"
          "Suspension: 140 mm front fork. Ideal for forest trails.", "T1.1", "doc-models", "https://northwind.example/bikes/trail-500",
          "Bikes > Trail 500", "Specs of the Trail 500 mountain bike: sizes, weight, gears, suspension",
          ("trail 500", "mountain bike", "mtb"), ("What sizes does the Trail 500 come in?", "How heavy is the Trail 500?")),
        s("S2", "City Cruiser commuter bike", "City Cruiser commuter bike\nFrame sizes: M, L. Weight: 15.8 kg. 7 gears.\n"
          "Comes with mudguards, rack and lights.", "T1.1", "doc-models", "https://northwind.example/bikes/city-cruiser",
          "Bikes > City Cruiser", "Specs of the City Cruiser commuter bike", ("city cruiser", "commuter"),
          ("Does the City Cruiser have lights?",)),
        s("S3", "Volt e-bike", "Volt electric bike\nFrame sizes: M, L. Weight: 22 kg. Range: up to 90 km.\n"
          "Battery: 500 Wh, charges in 4 hours.", "T1.1", "doc-models", "https://northwind.example/bikes/volt",
          "Bikes > Volt", "Specs of the Volt e-bike: range, battery, weight", ("volt", "e-bike", "electric"),
          ("How far can the Volt go on one charge?",)),
        s("S4", "Price list (old page)", "Prices\nTrail 500: $1,290\nCity Cruiser: $690\nVolt e-bike: $2,490\n"
          "Delivery fee: $49 per order.\nPrices include tax.", "T1.2", "doc-prices", "https://northwind.example/prices",
          "Prices", "Bike prices and delivery fee", ("price", "cost"), ("How much is the Trail 500?",),
          marks=(SupersedeMark(by="S9", lines=("Volt e-bike: $2,490",), evidence=("Volt e-bike: $2,290",)),)),
        s("S5", "Workshop services", "Workshop\nWe service and repair bikes of every brand.\nBasic tune-up: $59. Full service: $129.\n"
          "Book a slot: +1 555 0101.", "T2.1", "doc-workshop", "https://northwind.example/workshop", "Workshop",
          "Workshop repairs and service prices, booking", ("repair", "service", "tune-up"), ("Do you repair other brands?",)),
        s("S6", "Delivery", "Delivery\nWe deliver within 3-5 working days. Delivery fee: $49 per order, free above $1,500.",
          "T2.2", "doc-orders", "https://northwind.example/delivery", "Orders > Delivery", "Delivery times and fees",
          ("delivery", "shipping"), ("How long does delivery take?",)),
        s("S7", "Returns and warranty", "Returns\nUnused bikes can be returned within 30 days.\n"
          "Warranty: 5 years on frames, 2 years on parts.", "T2.2", "doc-orders", "https://northwind.example/returns",
          "Orders > Returns", "Return window and warranty", ("return", "warranty", "refund"), ("What is the warranty?",)),
        s("S8", "Contact", "Contact\nSales: +1 555 0100. Workshop: +1 555 0101. Email: hello@northwind.example.\n"
          "Shop hours: Mon-Sat 9:00-18:00.", "T2.2", "doc-contact", "https://northwind.example/contact", "Contact",
          "Phone numbers, email and opening hours", ("contact", "phone", "hours"), ("When are you open?",)),
        s("S9", "Approved price list 2026", "Approved price list\nTrail 500: $1,290\nCity Cruiser: $690\nVolt e-bike: $2,290\n"
          "Prices include tax.", "T1.2", "doc-approved", "", "Approved > Prices", "Current approved bike prices",
          ("price", "cost"), ("How much is the Volt?",), authority="approved"),
    )
    values = (
        Value("V1", "S4", "bike:trail_500", "price", 1290, "$1,290", "USD"),
        Value("V2", "S4", "bike:city_cruiser", "price", 690, "$690", "USD"),
        Value("V3", "S4", "bike:volt", "price", 2490, "$2,490", "USD", conflict_group="CG1", rank=1),
        Value("V4", "S9", "bike:trail_500", "price", 1290, "$1,290", "USD"),
        Value("V5", "S9", "bike:city_cruiser", "price", 690, "$690", "USD"),
        Value("V6", "S9", "bike:volt", "price", 2290, "$2,290", "USD", conflict_group="CG1"),
        Value("V7", "S6", "fee:delivery", "amount", 49, "$49", "USD"),
    )
    entities = (
        Entity("bike:trail_500", "bike", "Trail 500", ("trail", "trail 500 mtb")),
        Entity("bike:city_cruiser", "bike", "City Cruiser", ("cruiser",)),
        Entity("bike:volt", "bike", "Volt", ("volt e-bike", "e-bike")),
        Entity("fee:delivery", "fee", "Delivery fee"),
    )
    calcs = (Calculator(
        "order_total", "Order total", "price * quantity + fee", "bike", "Total for N bikes of one model including delivery",
        inputs=(CalcInput("price", "price"), CalcInput("fee", "amount", "fee:delivery")),
        user_inputs=(UserInput("quantity", "number of bikes ordered", "How many bikes would you like?", "bikes"),),
        applies_to=("bike:trail_500", "bike:city_cruiser", "bike:volt")),)
    return KnowledgeSnapshot(KBMeta(version, "Northwind Bikes", "A fictional bicycle shop"), nodes, sections, values,
                             entities, calcs)
