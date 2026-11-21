import json
import uuid

from novel_tts.domain.text import boundaries


class Edits:
    def save_edit(self, view, start, end, speaker, style, new_name=""):
        affected = [line for line in view.lines if line.start<end and line.end>start]
        if not affected or len({line.parent for line in affected})!=1:
            raise ValueError("请选择同一原文父段内的文字")
        safe = boundaries(view.source)
        if start>=end or start-view.offset not in safe or end-view.offset not in safe:
            raise ValueError("选区必须位于字形边界")
        parent = affected[0].parent
        with self.db:
            self.guard(view.book,view.revision)
            rows = [tuple(r) for r in self.db.execute("SELECT * FROM parts WHERE chapter=? AND parent=? ORDER BY start",
                                                       (view.chapter,parent))]
            if not rows or not rows[0][2]<=start<end<=rows[-1][3]:
                raise ValueError("选区超出父段")
            if new_name.strip():
                speaker = uuid.uuid4().hex
                self.db.execute("INSERT INTO characters VALUES(?,?,?,?)",
                                (view.book,speaker,new_name.strip(),f"{view.chapter}/{start}/{end}"))
            if speaker not in dict(self.characters(view.book)):
                raise ValueError("说话人不属于当前书籍")
            self.db.execute("INSERT INTO history(book,chapter,parent,previous,action) VALUES(?,?,?,?,?)",
                            (view.book,view.chapter,parent,json.dumps(rows,ensure_ascii=False),"人工标注"))
            kept = []
            for chapter,p,left,right,cid,old_style,manual in rows:
                if right<=start or left>=end:
                    kept.append((chapter,p,left,right,cid,old_style,manual))
                else:
                    if left<start:
                        kept.append((chapter,p,left,start,cid,old_style,manual))
                    if end<right:
                        kept.append((chapter,p,end,right,cid,old_style,manual))
            kept.append((view.chapter,parent,start,end,speaker,style,1))
            self.db.execute("DELETE FROM parts WHERE chapter=? AND parent=?", (view.chapter,parent))
            self.db.executemany("INSERT INTO parts VALUES(?,?,?,?,?,?,?)",kept)
            self.bump(view.book)
        return speaker

    def undo_edit(self, view):
        with self.db:
            self.guard(view.book,view.revision)
            row = self.db.execute("SELECT id,parent,previous FROM history WHERE chapter=? AND action='人工标注' ORDER BY id DESC LIMIT 1",(view.chapter,)).fetchone()
            if not row:
                raise ValueError("本章没有可撤销的标注")
            history_id,parent,previous = row
            self.db.execute("DELETE FROM parts WHERE chapter=? AND parent=?", (view.chapter,parent))
            self.db.executemany("INSERT INTO parts VALUES(?,?,?,?,?,?,?)",json.loads(previous))
            self.db.execute("UPDATE history SET action='已撤销' WHERE id=?", (history_id,))
            self.bump(view.book)

    def save_rule(self, view, line, scope, word, replacement):
        if not word or word not in line.display or not replacement.strip():
            raise ValueError("请填写本句内的原词和替换读法")
        scopes = {"单句":f"{view.book}/{view.chapter}/{line.start}","单书":view.book,"全局":"*"}
        with self.db:
            self.guard(view.book,view.revision)
            self.db.execute("INSERT OR REPLACE INTO dictionary VALUES(?,?,?)",(scopes[scope],word,replacement))
            if scope=="全局":
                self.db.execute("UPDATE books SET revision=revision+1")
            else:
                self.bump(view.book)

    def save_alias(self,view,cid,alias):
        if cid not in dict(self.characters(view.book)) or not alias.strip():
            raise ValueError("无效的别名或角色")
        with self.db:
            self.guard(view.book,view.revision)
            self.db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?)",(view.book,cid,alias.strip()))
            self.bump(view.book)

    def aliases(self, book):
        return tuple(tuple(r) for r in self.db.execute("SELECT id,alias FROM aliases WHERE book=? ORDER BY rowid",(book,)))
