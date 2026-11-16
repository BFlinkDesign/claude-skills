---
name: translate
description: Translate for Kirundi-speaking families: English into Kirundi with dates, times and numbers checked, and Kirundi into plain English. Use whenever the person asks to translate anything.
---

# Translate (English and Kirundi)

The person asking does not read Kirundi and is not technical. They cannot see a wrong date or a
sentence left in English. So the work is split: you understand the message and write the Kirundi;
the bundled script proves the details and writes them into the text itself. Follow the steps in
order, every time. Do not describe the steps, the script, or this skill to the person.

The script is `scripts/twc_check.py` in this skill's folder. Each command reads a JSON file:

```bash
python3 "<this skill's folder>/scripts/twc_check.py" scan < /tmp/twc.json; echo "exit=$?"
python3 "<this skill's folder>/scripts/twc_check.py" check < /tmp/twc.json; echo "exit=$?"
```

Write `/tmp/twc.json` with the file-writing tool so quotes and line breaks are escaped correctly.
`exit=0` means go on. `exit=2` means stop and fix: if the output has `repairs`, do what they say;
if it has `error`, your JSON was wrong, so fix it. Then run again.

## Steps

### 1. Find the message and the direction

Separate the person's instruction ("Translate:", "what does this say", "into Spanish") from the
text they want translated. If there is no text yet, ask for it and stop. Decide in this order:

1. **The instruction names target languages** (only words outside the text count, so "Spanish
   club meets Friday" names none). Kirundi or English as a target follows rows 2 and 3. For any
   other target language, translate into it normally and add one sentence saying the automatic
   checks cover Kirundi only. If Kirundi is named too, also do the Kirundi through steps 2 to 6.
2. **The text is English** and no other target is named: translate into Kirundi with steps 2 to
   6. Kirundi is the default; the person may never type the word "Kirundi".
3. **The text is not English** (Kirundi, usually a parent's reply, or any other language), or
   they ask what a text says: translate it into plain, natural English and stop here. Do not run
   the checker. Reply with this layout, word for word apart from the English and the language name:

   ````
   **In English**

   ```text
   <natural everyday English>
   ```

   **Status:** This is an AI reading of a <language> message. No <language> speaker has checked it. If it is about health, safety or money, ask a <language> speaker.
   ````

Default tone for Kirundi: warm, respectful, plain; use a tone they name.

### 2. List the facts and scan

A fact quotes the message word for word and says what it is. Each fact quotes **one detail and
nothing else**. List every one, in message order:

| The message has | Fact |
|---|---|
| a date | `{"text": "November 20, 2026", "is": "date", "value": "2026-11-20"}`; no year inside the quote: `"value": "11-20"` |
| a month with no day | `{"text": "December", "is": "month", "value": "12"}`; `"June 2027"` is `"2027-06"` |
| a day of the week | `{"text": "Friday", "is": "weekday", "value": "friday"}` |
| a clock time | `{"text": "3:15 p.m.", "is": "time", "value": "15:15"}` (24-hour); also `"noon"`, `"quarter to 4"`, `"three o'clock"` |
| a time with no a.m. or p.m. | `{"text": "3:15", "is": "time", "value": "3:15"}`, `{"text": "9", "is": "time", "value": "9:00"}`; never guess |
| anything to keep exactly: number, price, percent, phone, email, link, room, code | `{"text": "(515) 555-0142", "is": "copy"}`, `{"text": "-$12.40", "is": "copy"}` (keep a minus sign). Quote the number only: for "30 minutes" quote `"30"` and translate "minutes" |
| a number in words, or with an ending | `{"text": "fifteen", "is": "copy", "as": "15"}`, `{"text": "1st", "is": "copy", "as": "1"}`; `"as"` is for nothing else |
| an English name to keep that contains a day, a month or digits | `{"text": "Taco Tuesday", "is": "copy"}`, `{"text": "Mrs. Sun", "is": "copy"}`, `{"text": "April", "is": "copy"}` for a person |
| one ordinary word that only looks like a month or day | `{"text": "May", "is": "word"}` for "May I call you?" |

- Short day forms are weekday facts too: "M-F" is `"M"` (monday) and `"F"` (friday); "Tu/Th", "Weds".
- A range or list is one fact per end: "November 16-20" is `"November 16"` (`11-16`) and `"20"`
  (`11-20`); "7-8 p.m." is `"7"` (`19:00`) and `"8 p.m."` (`20:00`); "Monday-Friday" is two weekday facts.
- The script works out each 24-hour time itself, including a.m./p.m. borrowed across a range. If
  your value differs, the repair gives the value to use; use it.
- Quotes must not overlap. If the same text appears twice, list it twice.
- All-digit dates are read month first, as American schools write them: `11/12` is `11-12`.
- `3-5`, `1/2` on their own that are clearly a fraction, a score or a count, not a date: two copy
  facts (`"3"`, `"5"`), or one copy fact with `"confirmed": true`.
- A day number with no month near it ("Monday the 23rd") is a copy fact: `{"text": "23rd", "is": "copy", "as": "23"}`.

Run `scan` with `{"source": "<message>", "facts": [...]}`. Apply `repairs` and repeat until
`exit=0`.

If the scan exits 2 with an empty `repairs` and a non-empty `questions`, send the person those
questions, word for word, as your whole reply, and stop. When they answer, change the `value` of
the fact they corrected (the day or the date; for "3 o'clock in the afternoon", the time) and add
`"confirmed": true` to that fact, then scan again. On a date, time or weekday fact, use
`"confirmed"` only for an answer the person gave in this conversation. Add a question of your own only when the message cannot be translated
without it.

The scan lists each blank, such as `{2}`, and exactly what will be written there.

### 3. Translate with blanks

Read `references/kirundi-school-profile.md` and follow it. Write the Kirundi with the blank in
place of each fact, for example `Twoshobora kuvugana {1} {2}, isaha {3}?`

- Use every blank exactly once. Never type a digit, a month name, a weekday or a clock time
  yourself, in any language or in words.
- Keep every blank in the sentence it belongs to and the sentences in the order of the message.
  Keep dates in message order; the same for times, for weekdays, and for copied details.
- Write around the blank so it reads well: a date blank becomes `20 Munyonyo 2026`, a month
  blank `Kigarama`, a weekday blank `ku wa gatanu`, a time blank `15:15`.
- Do not add "morning", "evening" or similar next to a time unless the message says it.
- Translate every sentence and add none. Keep every question a question.
- Kirundi, not Kinyarwanda: `c`, `j`, `sh`, `vy` (never `cy`, `jy`, `shy`, `by`) and `-fise`
  (never `-fite`).
- Plain text only: no labels, notes or Markdown symbols the message does not have.

### 4. Read it back

Re-read only your Kirundi, with the blanks filled as the scan listed, and write in one or two
plain English sentences what it says. This is the only part the person can read, and the check
compares its days, months and a.m./p.m. times with the facts, so write what the Kirundi says, not
what the message said. Compare with the message: who does what, "not", how often,
must or may, how urgent. If anything differs, fix the Kirundi. Keep the sentences as `meaning`.

### 5. Check

Run `check` with `{"source": ..., "facts": [...], "template": "<Kirundi with blanks>", "meaning": "...", "files_dir": "<folder>"}`.
For `files_dir` use the folder whose files the person can download: `/mnt/user-data/outputs` when it
exists, otherwise a new folder such as `/tmp/twc-files`.
On `exit=2`, apply every item in `repairs` and run again. Repair at most twice; if the third run
still exits 2, go to step 6 with that result. Never change the message or drop a detail to pass.

### 6. Reply

Send the `reply` text from the last check exactly as printed: no greeting, no summary, no offer,
no explanation. When the check passed, its `files` lists two files the script wrote:
`Kirundi-message.html`, a page with large buttons to copy the message, download it as text, save
it as a picture or print it, and a three-step guide; and `Kirundi-message.txt`. Share both with
your tool for giving the person files (for example `present_files`), the page first, and do not
retype or change them. If you have no such tool, or the check reports `files_error`, send the
reply alone. A check that did not pass writes no files and removes earlier ones. When the check did not pass, the reply starts with "Do not send yet"; send it just
the same. Add one plain sentence after it only if they asked a direct question.

## If the script cannot run

If there is no tool for running code, or the script will not start or keeps answering with `error`, do not
claim any check. Fill the blanks yourself and send this layout with this status line, word for word:

````
**Kirundi message**

```text
<the Kirundi>
```

**What I meant it to say:** <your read-back>

**Status:** Automatic checks could not run, so this translation is unchecked. Compare every date, time and number with your message before you send it.
````

## Rules that never bend

- Never say or imply that a person reviewed the Kirundi. If they ask whether it is correct, say
  the details were filled in by the checker and the wording has not been read by a Kirundi speaker.
- Never add a fact, a diagnosis, blame, a promise, a consequence, a greeting or a closing that
  the message does not contain.
- Never translate or respell a person's name, a school name or a program name.
- If they ask for a change, make it and run steps 2 to 6 again on the new wording.
- The automatic checks cover English into Kirundi only. Never show the checker's status line on
  anything else.

## Example

Message: `Could we talk on Friday, November 20, 2026, at 3:15 p.m.?`

Facts: `Friday` (weekday, friday), `November 20, 2026` (date, 2026-11-20), `3:15 p.m.` (time, 15:15).
Template: `<Kirundi words> {1} {2}, <Kirundi words> {3}?`

Reply, as printed by `check`:

````
**Kirundi message**

```text
<Kirundi words> ku wa gatanu 20 Munyonyo 2026, <Kirundi words> 15:15?
```

**What I meant it to say:** Could we talk on Friday, November 20, 2026, at 3:15 in the afternoon?

**Status:** Dates, times, numbers and contact details were filled in from your message by the checker. No Kirundi speaker has checked the wording.
````
