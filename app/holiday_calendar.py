"""Festivos nacionales de Colombia vigentes para 2026–2030.

Fuentes:
- Ley 51 de 1983 (Emiliani): https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=4954
- Ley 2578 de 2026, artículo 6: https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=193309
No incluye días cívicos, cierres locales ni suspensiones de términos particulares.
"""
from datetime import date, timedelta


def easter_sunday(year: int) -> date:
    """Calendario gregoriano (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    n = h + l - 7*m + 114
    return date(year, n // 31, n % 31 + 1)


def next_monday(day: date) -> date:
    return day + timedelta(days=(7 - day.weekday()) % 7)


def colombia_holidays(start: int = 2026, end: int = 2030) -> dict[date, str]:
    """Días de descanso remunerado nacionales y traslado Emiliani, sin duplicar fecha."""
    if start > end or start < 1900 or end > 2100:
        raise ValueError('Rango de años inválido.')
    dates: dict[date, list[str]] = {}
    def add(day: date, name: str):
        dates.setdefault(day, [])
        if name not in dates[day]:
            dates[day].append(name)
    fixed = ((1, 1, 'Año Nuevo'), (5, 1, 'Día del Trabajo'),
             (7, 20, 'Día de la Independencia'), (8, 7, 'Batalla de Boyacá'),
             (12, 8, 'Inmaculada Concepción'), (12, 25, 'Navidad'))
    emiliani = ((1, 6, 'Reyes Magos'), (3, 19, 'San José'),
                (6, 29, 'San Pedro y San Pablo'), (8, 15, 'Asunción de la Virgen'),
                (10, 12, 'Día de la Diversidad Étnica'), (11, 1, 'Todos los Santos'),
                (11, 11, 'Independencia de Cartagena'),
                (7, 9, 'Nuestra Señora del Rosario de Chiquinquirá'))
    for year in range(start, end + 1):
        for month, day, name in fixed:
            add(date(year, month, day), name)
        for month, day, name in emiliani:
            add(next_monday(date(year, month, day)), name)
        easter = easter_sunday(year)
        add(easter - timedelta(days=3), 'Jueves Santo')
        add(easter - timedelta(days=2), 'Viernes Santo')
        add(easter + timedelta(days=43), 'Ascensión del Señor')
        add(easter + timedelta(days=64), 'Corpus Christi')
        add(easter + timedelta(days=71), 'Sagrado Corazón de Jesús')
    return dict(sorted((day, ' / '.join(names)) for day, names in dates.items()))
