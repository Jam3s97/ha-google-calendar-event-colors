"""Split a Google calendar into one virtual calendar per event colour label.

Google Calendar has two event colour mechanisms:

- `colorId` 1-11: the legacy fixed palette. Used here only as the read
  fallback for events without a label.
- `eventLabelId`: per-calendar labels, one per colour in the current Google
  Calendar colour picker (24). Users can name them (e.g. "Child1"). Labels
  are writable through the API, but only when the request has the query
  parameter `eventLabelVersion=1`; without it, `eventLabelId` is ignored.

Events left on the calendar's default colour carry neither. They are bucketed
with the label that matches the calendar's default colour when that label is
named, otherwise with "Other".
"""

from dataclasses import dataclass
from typing import Final

from gcal_sync.model import CalendarBasic, Event

OTHER: Final = "other"


@dataclass(frozen=True, kw_only=True)
class Colour:
    """A colour in the Google Calendar picker."""

    name: str
    label_color: str
    """Hex colour of an event label with this colour."""
    event_color_id: str | None
    """Legacy event `colorId`, or None for colours added after the legacy palette."""
    calendar_color: str
    """Legacy calendar palette hex reported by the calendar list for this colour."""


COLOURS: Final = (
    Colour(
        name="Lavender",
        label_color="#7986cb",
        event_color_id="1",
        calendar_color="#9a9cff",
    ),
    Colour(
        name="Sage", label_color="#33b679", event_color_id="2", calendar_color="#92e1c0"
    ),
    Colour(
        name="Grape",
        label_color="#8e24aa",
        event_color_id="3",
        calendar_color="#cd74e6",
    ),
    Colour(
        name="Flamingo",
        label_color="#e67c73",
        event_color_id="4",
        calendar_color="#d06b64",
    ),
    Colour(
        name="Banana",
        label_color="#f6bf26",
        event_color_id="5",
        calendar_color="#fad165",
    ),
    Colour(
        name="Tangerine",
        label_color="#f4511e",
        event_color_id="6",
        calendar_color="#fa573c",
    ),
    Colour(
        name="Peacock",
        label_color="#039be5",
        event_color_id="7",
        calendar_color="#9fe1e7",
    ),
    Colour(
        name="Graphite",
        label_color="#616161",
        event_color_id="8",
        calendar_color="#c2c2c2",
    ),
    Colour(
        name="Blueberry",
        label_color="#3f51b5",
        event_color_id="9",
        calendar_color="#4986e7",
    ),
    Colour(
        name="Basil",
        label_color="#0b8043",
        event_color_id="10",
        calendar_color="#16a765",
    ),
    Colour(
        name="Tomato",
        label_color="#d50000",
        event_color_id="11",
        calendar_color="#f83a22",
    ),
    Colour(
        name="Radicchio",
        label_color="#ad1457",
        event_color_id=None,
        calendar_color="#cca6ac",
    ),
    Colour(
        name="Cherry blossom",
        label_color="#d81b60",
        event_color_id=None,
        calendar_color="#f691b2",
    ),
    Colour(
        name="Pumpkin",
        label_color="#ef6c00",
        event_color_id=None,
        calendar_color="#ff7537",
    ),
    Colour(
        name="Mango",
        label_color="#f09300",
        event_color_id=None,
        calendar_color="#ffad46",
    ),
    Colour(
        name="Citron",
        label_color="#e4c441",
        event_color_id=None,
        calendar_color="#fbe983",
    ),
    Colour(
        name="Avocado",
        label_color="#c0ca33",
        event_color_id=None,
        calendar_color="#b3dc6c",
    ),
    Colour(
        name="Pistachio",
        label_color="#7cb342",
        event_color_id=None,
        calendar_color="#7bd148",
    ),
    Colour(
        name="Eucalyptus",
        label_color="#009688",
        event_color_id=None,
        calendar_color="#42d692",
    ),
    Colour(
        name="Cobalt",
        label_color="#4285f4",
        event_color_id=None,
        calendar_color="#9fc6e7",
    ),
    Colour(
        name="Wisteria",
        label_color="#b39ddb",
        event_color_id=None,
        calendar_color="#b99aff",
    ),
    Colour(
        name="Amethyst",
        label_color="#9e69af",
        event_color_id=None,
        calendar_color="#a47ae2",
    ),
    Colour(
        name="Birch",
        label_color="#a79b8e",
        event_color_id=None,
        calendar_color="#cabdbf",
    ),
    Colour(
        name="Cocoa",
        label_color="#795548",
        event_color_id=None,
        calendar_color="#ac725e",
    ),
)

_BY_LABEL_COLOR: Final = {colour.label_color: colour for colour in COLOURS}
_BY_EVENT_COLOR_ID: Final = {
    colour.event_color_id: colour for colour in COLOURS if colour.event_color_id
}
_BY_CALENDAR_COLOR: Final = {colour.calendar_color: colour for colour in COLOURS}


@dataclass(frozen=True, kw_only=True)
class ColourLabel:
    """An event colour label defined on one calendar."""

    id: str
    name: str | None
    """Name the user gave the label in Google Calendar, if any."""
    background_color: str
    colour: Colour | None
    """The picker colour, or None if Google returns an unknown colour."""

    @property
    def display_name(self) -> str:
        """Return the label name, falling back to the colour name."""
        if self.name:
            return self.name
        if self.colour:
            return self.colour.name
        return self.background_color

    @property
    def event_color_id(self) -> str | None:
        """Return the legacy `colorId` of this label's colour, if any."""
        return self.colour.event_color_id if self.colour else None


class ColourSplit:
    """Assigns each event of one calendar to a label bucket or OTHER."""

    def __init__(
        self, calendar: CalendarBasic, calendar_background_color: str | None
    ) -> None:
        """Build the split from the calendar's labels and its default colour."""
        self.labels: dict[str, ColourLabel] = {}
        label_properties = calendar.label_properties
        for label in label_properties.event_labels if label_properties else []:
            color = label.background_color.lower()
            self.labels[label.id] = ColourLabel(
                id=label.id,
                name=label.name or None,
                background_color=color,
                colour=_BY_LABEL_COLOR.get(color),
            )
        by_colour = {
            label.colour.name: label for label in self.labels.values() if label.colour
        }
        self._label_by_color_id = {
            color_id: by_colour[colour.name]
            for color_id, colour in _BY_EVENT_COLOR_ID.items()
            if colour.name in by_colour
        }
        default_colour = _BY_CALENDAR_COLOR.get(
            (calendar_background_color or "").lower()
        )
        default_label = by_colour.get(default_colour.name) if default_colour else None
        self.default_label: ColourLabel | None = (
            default_label if default_label and default_label.name else None
        )
        """The named label that events on the calendar's default colour belong to."""

    @property
    def named_labels(self) -> list[ColourLabel]:
        """Return the labels the user has named."""
        return [label for label in self.labels.values() if label.name]

    def bucket(self, event: Event) -> str:
        """Return the label id the event belongs to, or OTHER."""
        if event.event_label_id:
            return (
                event.event_label_id if event.event_label_id in self.labels else OTHER
            )
        if event.color_id:
            label = self._label_by_color_id.get(event.color_id)
            return label.id if label else OTHER
        return self.default_label.id if self.default_label else OTHER
