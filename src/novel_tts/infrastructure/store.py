"""Transaction methods used exclusively by the repository thread."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from novel_tts.domain.annotations import MockLLM, validate
from novel_tts.domain.models import AnnotationRequest, Parent, Stale, Ticket
from novel_tts.domain.text import chapters, parents

SCHEMA = """
CREATE TABLE books(id TEXT PRIMARY KEY,title TEXT NOT NULL,digest TEXT,encoding TEXT,raw BLOB,revision INTEGER DEFAULT 0,opened REAL);
CREATE TABLE chapters(id TEXT PRIMARY KEY,book TEXT REFERENCES books ON DELETE CASCADE,ordinal INTEGER,title TEXT,start INTEGER);
CREATE TABLE pages(chapter TEXT REFERENCES chapters ON DELETE CASCADE,num INTEGER,start INTEGER,source TEXT,PRIMARY KEY(chapter,num));
CREATE TABLE parts(chapter TEXT REFERENCES chapters ON DELETE CASCADE,parent TEXT,start INTEGER,end INTEGER,speaker TEXT,style TEXT,manual INTEGER DEFAULT 0,PRIMARY KEY(chapter,start));
CREATE TABLE characters(book TEXT REFERENCES books ON DELETE CASCADE,id TEXT,name TEXT,evidence TEXT,PRIMARY KEY(book,id));
CREATE TABLE bindings(book TEXT,id TEXT,profile TEXT,voice TEXT,manual INTEGER DEFAULT 0,PRIMARY KEY(book,id,profile),FOREIGN KEY(book,id) REFERENCES characters ON DELETE CASCADE);
CREATE TABLE progress(book TEXT PRIMARY KEY REFERENCES books ON DELETE CASCADE,chapter TEXT REFERENCES chapters ON DELETE CASCADE,cp INTEGER,hash TEXT,ms INTEGER,rate REAL);
CREATE TABLE dictionary(scope TEXT,word TEXT,replacement TEXT,PRIMARY KEY(scope,word));
CREATE TABLE history(id INTEGER PRIMARY KEY,book TEXT REFERENCES books ON DELETE CASCADE,chapter TEXT,parent TEXT,previous TEXT,action TEXT);
CREATE TABLE audio(hash TEXT PRIMARY KEY,digest TEXT,frames INTEGER,last_used REAL);
CREATE TABLE jobs(id TEXT PRIMARY KEY,attempt TEXT,hash TEXT,book TEXT REFERENCES books ON DELETE CASCADE,revision INTEGER,state TEXT);
CREATE INDEX job_lookup ON jobs(book,state);
"""


@dataclass(frozen=True)
class Line:
    parent: str
    start: int
    end: int
    speaker: str
    style: str
    manual: bool
    display: str
    spoken: str
    voice: str
    substitute: bool


@dataclass(frozen=True)
class View:
    book: str
    chapter: str
    title: str
    revision: int
    page: int
    page_count: int
    offset: int
    source: str
    lines: tuple[Line, ...]
    characters: tuple[tuple[str, str], ...]


def page_chunks(source):
    start = 0
    page = []
    length = 0
    for p in parents(source):
        if page and length + len(p.text) > 8000:
            yield start, "".join(page)
            start += length
            page, length = [], 0
        page.append(p.text)
        length += len(p.text)
    if page:
        yield start, "".join(page)


from novel_tts.infrastructure.edits import Edits


from novel_tts.infrastructure.cache import CacheIndex


class Store(Edits,CacheIndex):
    def __init__(self, paths):
        self.paths = paths
        self.path = paths.data / "library.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
            self.db.close()
            raise RuntimeError("数据库来自更新版本")
        if version == 0:
            if self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                self.db.close()
                raise RuntimeError("未知数据库结构，请先恢复备份")
            self.db.executescript("BEGIN;" + SCHEMA + "PRAGMA user_version=1; COMMIT;")

        if version < 2:
            if version:
                self.backup(self.path.with_suffix(".before-v2.sqlite3"))
            self.db.executescript("""BEGIN;
                CREATE TABLE aliases(book TEXT,id TEXT,alias TEXT,PRIMARY KEY(book,id,alias),
                  FOREIGN KEY(book,id) REFERENCES characters ON DELETE CASCADE);
                PRAGMA user_version=2; COMMIT;""")

        if version < 3:
            if version:
                self.backup(self.path.with_suffix(".before-v3.sqlite3"))
            self.db.executescript("""BEGIN;
                CREATE TABLE attempts(id TEXT PRIMARY KEY,book TEXT,stamp REAL,record TEXT);
                PRAGMA user_version=3; COMMIT;""")
        self.recovery = self.recover()

    def record_attempt(self,book,row):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)",
                            (row["attempt"],book,time.time(),json.dumps(row,ensure_ascii=False)))

    def attempts(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT record FROM attempts ORDER BY stamp DESC LIMIT 100")]

    def close(self):
        self.db.close()

    def backup(self, destination):
        with sqlite3.connect(destination) as dest:
            self.db.backup(dest)

    def library(self):
        return [tuple(r) for r in self.db.execute("SELECT id,title FROM books ORDER BY opened DESC")]

    def toc(self, book):
        return [tuple(r) for r in self.db.execute("SELECT id,title FROM chapters WHERE book=? ORDER BY ordinal", (book,))]

    def add_book(self, draft, cuts, cancel):
        book = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO books VALUES(?,?,?,?,?,0,?)", (
                book, draft.title, draft.digest, draft.encoding, draft.raw, time.time()))
            self.db.executemany("INSERT INTO characters VALUES(?,?,?,?)", [
                (book, "narrator", "旁白", ""), (book, "unknown", "待确认", "")])
            for i, (offset, title, source) in enumerate(chapters(draft, cuts)):
                chapter = uuid.uuid4().hex
                self.db.execute("INSERT INTO chapters VALUES(?,?,?,?,?)", (chapter, book, i, title, offset))
                for n, (start, text) in enumerate(page_chunks(source)):
                    if cancel.is_set():
                        from novel_tts.domain.models import Cancelled
                        raise Cancelled()
                    self.db.execute("INSERT INTO pages VALUES(?,?,?,?)", (chapter, n, start, text))
        return book

    def delete_book(self, book):
        with self.db:
            self.db.execute("DELETE FROM dictionary WHERE scope=? OR scope LIKE ?", (book, book + "/%"))
            self.db.execute("DELETE FROM books WHERE id=?", (book,))

    def progress(self, book):
        row = self.db.execute("SELECT chapter,cp,hash,ms,rate FROM progress WHERE book=?", (book,)).fetchone()
        return tuple(row) if row else None

    def save_progress(self, book, chapter, cp, key, ms, rate):
        with self.db:
            if self.db.execute("SELECT 1 FROM chapters WHERE id=? AND book=?", (chapter,book)).fetchone():
                self.db.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?,?)", (book,chapter,cp,key,ms,rate))
                self.db.execute("UPDATE books SET opened=? WHERE id=?", (time.time(), book))

    def page_at(self, chapter, cp):
        row = self.db.execute("SELECT num FROM pages WHERE chapter=? AND start<=? ORDER BY start DESC LIMIT 1", (chapter,cp)).fetchone()
        return row[0] if row else 0

    def revision(self, book):
        row = self.db.execute("SELECT revision FROM books WHERE id=?", (book,)).fetchone()
        if not row:
            raise Stale("书籍已删除")
        return row[0]

    def guard(self, book, revision):
        if self.revision(book) != revision:
            raise Stale("内容修订已更新")

    def bump(self, book):
        self.db.execute("UPDATE books SET revision=revision+1 WHERE id=?", (book,))

    def characters(self, book):
        return tuple(tuple(r) for r in self.db.execute("SELECT id,name FROM characters WHERE book=? ORDER BY rowid", (book,)))

    def request(self, chapter, page):
        row = self.db.execute("""SELECT b.id,b.digest,b.revision,p.start,p.source FROM pages p
            JOIN chapters c ON p.chapter=c.id JOIN books b ON c.book=b.id WHERE p.chapter=? AND p.num=?""",
            (chapter,page)).fetchone()
        if not row:
            raise Stale("章节已删除")
        targets = tuple(Parent(f"p{row[3]+p.start}", row[3]+p.start, p.text) for p in parents(row[4]))
        prev = self.db.execute("SELECT substr(source,-256) FROM pages WHERE chapter=? AND num=?", (chapter,page-1)).fetchone()
        return AnnotationRequest(row[0],chapter,row[1],row[2],uuid.uuid4().hex,targets,prev[0] if prev else "",self.characters(row[0]))

    def commit_annotations(self, req, result, caps, initial=False):
        validate(req,result)
        with self.db:
            self.guard(req.book,req.revision)
            owner = self.db.execute("SELECT book FROM chapters WHERE id=?", (req.chapter,)).fetchone()
            if not owner or owner[0] != req.book:
                raise Stale("章节不属于此书")
            refs = {}
            for candidate in result.candidates:
                evidence = f"{req.chapter}/{candidate.span_id}/{candidate.start}/{candidate.end}/{candidate.ref}"
                cid = uuid.uuid5(uuid.UUID(req.book), evidence).hex
                self.db.execute("INSERT OR IGNORE INTO characters VALUES(?,?,?,?)", (req.book,cid,candidate.name,evidence))
                refs[candidate.ref] = cid
                self.binding(req.book,cid,caps)
            for p, (_, parts) in zip(req.targets,result.lines):
                if self.db.execute("SELECT 1 FROM parts WHERE chapter=? AND parent=? AND manual=1", (req.chapter,p.id)).fetchone():
                    continue
                self.db.execute("DELETE FROM parts WHERE chapter=? AND parent=?", (req.chapter,p.id))
                self.db.executemany("INSERT INTO parts VALUES(?,?,?,?,?,?,0)", [
                    (req.chapter,p.id,p.start+part.start,p.start+part.end,refs.get(part.speaker,part.speaker),part.style)
                    for part in parts])
            if not initial:
                self.bump(req.book)

    def binding(self, book, cid, caps):
        profile = caps.provider + "/" + caps.model
        voices = caps.voices
        if not voices:
            raise ValueError("所选语音配置没有可用音色")
        row = self.db.execute("SELECT voice FROM bindings WHERE book=? AND id=? AND profile=?", (book,cid,profile)).fetchone()
        if row:
            return (row[0],False) if row[0] in voices else (voices[0], True)
        voice = voices[0]
        if cid not in ("narrator","unknown") and len(voices) > 1:
            count = self.db.execute("SELECT count(*) FROM bindings WHERE book=? AND profile=? AND id NOT IN ('narrator','unknown')", (book,profile)).fetchone()[0]
            voice = voices[1+count % (len(voices)-1)]
        self.db.execute("INSERT INTO bindings VALUES(?,?,?,?,0)", (book,cid,profile,voice))
        return voice,False

    def view(self, chapter, page, caps):
        row = self.db.execute("""SELECT c.book,c.title,p.start,p.source,(SELECT count(*) FROM pages WHERE chapter=c.id)
            FROM chapters c JOIN pages p ON c.id=p.chapter WHERE c.id=? AND p.num=?""",(chapter,page)).fetchone()
        if not row:
            raise Stale("找不到章节页")
        book,title,offset,source,count = row
        if not self.db.execute("SELECT 1 FROM parts WHERE chapter=? AND start=?", (chapter,offset)).fetchone():
            req = self.request(chapter,page)
            self.commit_annotations(req,MockLLM().analyze_chunk(req),caps,initial=True)
        rows = self.db.execute("""SELECT parent,start,end,speaker,style,manual FROM parts
            WHERE chapter=? AND start>=? AND start<? AND speaker!='skip' ORDER BY start""",
            (chapter,offset,offset+len(source))).fetchall()
        lines = []
        with self.db:
            rules = self.rules(book,chapter)
            for parent,start,end,cid,style,manual in rows:
                display = source[start-offset:end-offset]
                voice,subst = self.binding(book,cid,caps)
                spoken = self.spoken(rules, start, display)
                lines.append(Line(parent,start,end,cid,style,bool(manual),display,spoken,voice,subst))
        return View(book,chapter,title,self.revision(book),page,count,offset,source,tuple(lines),self.characters(book))

    def rules(self, book, chapter):
        scopes = ["*",book]
        rows = self.db.execute("SELECT scope,word,replacement FROM dictionary WHERE scope IN (?,?) OR scope LIKE ?",
                               (*scopes,book+"/"+chapter+"/%")).fetchall()
        global_rules, book_rules, lines = {}, {}, {}
        for scope,word,repl in rows:
            if scope=="*":
                global_rules[word]=repl
            elif scope==book:
                book_rules[word]=repl
            else:
                lines.setdefault(int(scope.rsplit("/",1)[1]),{})[word]=repl
        return global_rules | book_rules, lines

    @staticmethod
    def spoken(rules, start, display):
        import re
        terms = rules[0] | rules[1].get(start,{})
        if not terms:
            return display
        pattern = "|".join(re.escape(s) for s in sorted(terms,key=len,reverse=True))
        return re.sub(pattern,lambda m:terms[m.group()],display)

    def set_voice(self, view, cid, caps, voice):
        if voice not in caps.voices:
            raise ValueError("音色不在可用列表")
        with self.db:
            self.guard(view.book,view.revision)
            self.db.execute("INSERT OR REPLACE INTO bindings VALUES(?,?,?,?,1)", (view.book,cid,caps.provider+"/"+caps.model,voice))
            self.bump(view.book)

    def audio_info(self, key):
        row = self.db.execute("SELECT digest,frames FROM audio WHERE hash=?", (key,)).fetchone()
        if row:
            with self.db:
                self.db.execute("UPDATE audio SET last_used=? WHERE hash=?", (time.time(),key))
        return tuple(row) if row else None

    def begin(self, ticket: Ticket):
        with self.db:
            self.guard(ticket.book,ticket.revision)
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,'running')",
                (ticket.job_id,ticket.attempt_id,ticket.request_hash,ticket.book,ticket.revision))

    def finish_audio(self, ticket, artifact):
        with self.db:
            self.guard(ticket.book,ticket.revision)
            cur = self.db.execute("""UPDATE jobs SET state='done' WHERE id=? AND attempt=? AND hash=? AND state='running'""",
                                  (ticket.job_id,ticket.attempt_id,ticket.request_hash))
            if not cur.rowcount or artifact.request.key != ticket.request_hash:
                raise Stale("合成尝试已过期")
            self.db.execute("INSERT OR REPLACE INTO audio VALUES(?,?,?,?)", (ticket.request_hash,artifact.sha256,artifact.frames,time.time()))

    def end_job(self, ticket, state):
        with self.db:
            self.db.execute("UPDATE jobs SET state=? WHERE id=? AND attempt=? AND state='running'", (state,ticket.job_id,ticket.attempt_id))
