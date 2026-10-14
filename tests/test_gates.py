from frame_curator import art


def test_strict_gate_blocks_canon_traps():
    for t in ["Head of Saint John the Baptist", "Judith with the Head of Holofernes", "The Crucifixion",
              "Diana and Actaeon", "Bathers at Asnières", "The Battle of Anghiari", "Perseus's Last Duty"]:
        assert not art._subject_safe(t), t


def test_strict_gate_keeps_place_names():
    for t in ["Garden at Sainte-Adresse", "The Seine at Saint-Mammès", "Marseille, the Old Port", "View of Toledo",
              "Floral Still Life", "View from Mount Holyoke, Northampton, Massachusetts, after a Thunderstorm—The Oxbow"]:
        assert art._subject_safe(t), t


def test_core_gate_allows_history_but_not_gore():
    assert not art._subject_safe("Washington Crossing the Delaware", "Soldiers")              # strict
    assert art._subject_safe("Washington Crossing the Delaware", "Soldiers", strict=False)    # core
    assert not art._subject_safe("The Wounded Indian Slaking His Death Thirst", strict=False)
    assert not art._subject_safe("The Bather", strict=False)


def test_era_gate():
    assert not art._era_ok("Max Beckmann", "1949")
    assert art._era_ok("Claude Monet", "1919")
    assert not art._era_ok("Someone", "1920")
    assert art._era_ok("Camille Corot", "1850")
