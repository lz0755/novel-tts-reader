import re

from novel_tts.domain.voices import dialogue_voice_types, identity_voice_type


def _named_clues(db, chapter, name):
    mentions = re.compile(re.escape(name))
    for offset, source in db.execute(
        "SELECT start,source FROM pages WHERE chapter=? AND instr(source,?)",
        (chapter, name),
    ):
        for start, end in db.execute(
            """SELECT start,end FROM parts WHERE chapter=? AND speaker='narrator'
               AND start>=? AND start<?""", (chapter, offset, offset + len(source))
        ):
            text = source[start - offset:end - offset]
            for match in mentions.finditer(text):
                yield identity_voice_type(
                    name, text[max(0, match.start() - 16):match.start()],
                    text[match.end():match.end() + 16],
                )


def _anchor_context(db, name, anchor):
    if len(anchor) != 5 or not anchor[1].startswith("p"):
        return "", ""
    offsets = (anchor[1][1:], anchor[2], anchor[3])
    if not all(value.isdecimal() for value in offsets):
        return "", ""
    parent, start, end = map(int, offsets)
    page = db.execute(
        """SELECT start,source FROM pages WHERE chapter=? AND start<=?
           ORDER BY start DESC LIMIT 1""",
        (anchor[0], parent),
    ).fetchone()
    if page:
        source = page[1]
        base = parent - page[0]
        cue = source[base + start:base + end]
        if cue.count(name) == 1:
            pos = base + start + cue.index(name)
            return source[max(0, pos - 16):pos], source[pos + len(name):pos + len(name) + 16]
    return "", ""


def infer_voice_type(db, book, cid):
    """Read clues belonging to this role in previously saved annotations."""
    role = db.execute(
        """SELECT c.name,c.evidence,h.voice_type FROM characters c
           LEFT JOIN voice_hints h USING(book,id) WHERE c.book=? AND c.id=?""",
        (book, cid),
    ).fetchone()
    if not role or role[2]:
        return role[2] if role else None
    name, evidence, _ = role
    anchor = (evidence or "").split("/", 4)
    kind = identity_voice_type(name, *_anchor_context(db, name, anchor))
    kinds = {kind} if kind else set()
    # A later introduction in the same chapter can identify a named role.
    # For homonyms, only the role's own evidence anchor and assigned lines count.
    if len(name) >= 2 and anchor[0] and not db.execute(
        """SELECT 1 FROM characters WHERE book=? AND name=? AND id<>?
           UNION ALL SELECT 1 FROM aliases WHERE book=? AND alias=? AND id<>? LIMIT 1""",
        (book, name, cid, book, name, cid),
    ).fetchone():
        kinds.update(k for k in _named_clues(db, anchor[0], name) if k)
    # Use the attribution immediately outside each assigned part. A quotation
    # can refer to a listener or someone else of a different gender.
    rows = db.execute(
        """SELECT p.style,
                  substr(g.source, max(1,p.start-g.start-47), min(48,p.start-g.start)),
                  substr(g.source, p.end-g.start+1, 48)
           FROM parts p JOIN chapters c ON c.id=p.chapter
           JOIN pages g ON g.chapter=p.chapter AND g.start=(
               SELECT start FROM pages WHERE chapter=p.chapter AND start<=p.start
               ORDER BY start DESC LIMIT 1)
           WHERE c.book=? AND p.speaker=?""",
        (book, cid),
    )
    for style, left, right in rows:
        kinds.update(dialogue_voice_types(left, right, style))
        if len(kinds) > 1:
            return None
    return kinds.pop() if len(kinds) == 1 else None
