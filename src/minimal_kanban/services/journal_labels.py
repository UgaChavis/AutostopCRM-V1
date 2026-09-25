"""Shared Russian period labels for the cash journal and card activity log."""

from datetime import datetime

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def day_label(date_key: str) -> str:
    try:
        value = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return date_key
    return f"{value.strftime('%d.%m.%Y')}, {_WEEKDAYS[value.weekday()]}"


def week_label(week_key: str) -> str:
    try:
        year_text, week_text = week_key.split("-W", 1)
        if (
            not year_text.isdecimal()
            or not week_text.isdecimal()
            or len(year_text) != 4
            or len(week_text) > 2
        ):
            return week_key
        start = datetime.fromisocalendar(int(year_text), int(week_text), 1)
        end = datetime.fromisocalendar(int(year_text), int(week_text), 7)
    except (ValueError, TypeError):
        return week_key
    return f"{week_text} неделя: {start.strftime('%d.%m')} - {end.strftime('%d.%m.%Y')}"


def month_label(month_key: str) -> str:
    try:
        value = datetime.strptime(month_key, "%Y-%m")
    except ValueError:
        return month_key
    return f"{_MONTHS[value.month - 1]} {value.year}"
