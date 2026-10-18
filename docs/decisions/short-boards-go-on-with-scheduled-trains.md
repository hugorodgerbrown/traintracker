# Short boards go on with scheduled trains

Status: accepted (2026-10-18)

## Context

Darwin answers a board for two hours at most. On a quiet line that is one or
two trains, or none: Kildonan, on the Far North Line, was blank at 10:55 on a
Sunday because its next train was at 13:28. Overnight even Cambridge is blank
from about 01:30 until 04:45. The shared boards are left open on screens and
embedded in other sites, so a blank board can stay on show for hours. The
timetable the server already loads each day knows every one of those trains
(TRA-33; mockups in the issue).

## Decision

- **Fill to the board's rows, with no threshold.** A live board with fewer
  trains than it asked for goes on with scheduled trains from the timetable.
  A busy station fills its rows from Darwin long before two hours, so it never
  shows a scheduled train, and there is no number of trains to tune.
- **From where Darwin's window ends, up to a day ahead.** The timetable is
  read from two hours after the board's start, so a train is never on it
  twice; a train at the same time and destination as a live one is dropped as
  well. Six hours is read first and a day only if that is not enough, which
  covers the longest overnight gap without reading a busy station's whole day.
- **Marked in Expected, in words.** A scheduled train says SCHEDULED where a
  live one says ON TIME or its expected time, in pale letters, since amber is
  the live, on-time colour and white means look. "Booked" was rejected:
  Traintrackr sells no tickets, so it read as a reservation. A train on a
  later day comes under that day's name (MONDAY 19 OCTOBER).
- **The tools fill too.** `live_departures`, `live_arrivals` and `show_board`
  fill the same way; each added train has `source: "timetable"` and a `date`.
  `departure_platform` and `platform_departures` answer about the next two
  hours and do not.
- **A board that can't be filled stays as Darwin gave it.** Without a
  timetable, or when timetable reads are all taken, nothing fails.

## Consequences

- A short board costs one or two timetable reads a minute, shared by every
  viewer of that board in the minute, as its Darwin fetch is.
- A scheduled train shows no on-the-day delay or cancellation until it is
  within two hours; the board says so in a note.
