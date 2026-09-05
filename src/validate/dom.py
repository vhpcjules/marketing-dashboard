"""A small, tolerant DOM over html.parser. No third-party dependency.

bs4 and lxml are not installed here and the gate must run standalone in the
build, so this is the whole HTML model the checks share. It is deliberately
forgiving on input - v1 pages have stray close tags and unclosed <li>s - and
deliberately strict on output: `rendered_text()` is what a reader sees, and
nothing else. Scripts, styles, and attributes never leak into it, which is
the property the language checks depend on ("phone caPTure" in a heading,
"font-size:12pt" in a style attribute, and `oPPortunity` in a data key are
all invisible to a word-boundary regex over rendered text).

Tag balance is a separate, raw pass (`tag_counts`) because a tolerant tree
hides exactly the thing that check is looking for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterator

__all__ = ["Node", "parse", "tag_counts", "VOID_ELEMENTS", "TEXT", "DOCUMENT"]

TEXT = "#text"
DOCUMENT = "#document"

# Elements that never have children or a close tag. Treating these as
# containers would swallow the rest of the document into an <img>.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

# Content that is not rendered as text.
NON_RENDERED = frozenset({"script", "style", "template", "noscript"})

# Elements whose edges separate words when read.
BLOCK_ELEMENTS = frozenset({
    "address", "article", "aside", "blockquote", "body", "br", "button", "caption", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr", "html", "li", "main", "nav",
    "ol", "option", "p", "pre", "section", "select", "summary", "table", "tbody", "td",
    "tfoot", "th", "thead", "title", "tr", "ul",
})

_WS = re.compile(r"\s+")


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)
    text: str = ""       # text nodes only
    line: int = 0        # 1-based source line of the start tag

    # -- structure ------------------------------------------------------

    @property
    def is_text(self) -> bool:
        return self.tag == TEXT

    @property
    def is_element(self) -> bool:
        return self.tag not in (TEXT, DOCUMENT)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes()

    def ancestors(self) -> Iterator["Node"]:
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def closest(self, pred) -> "Node | None":
        """Self or nearest ancestor satisfying pred."""
        n: Node | None = self
        while n is not None:
            if n.is_element and pred(n):
                return n
            n = n.parent
        return None

    def has_ancestor_attr(self, name: str) -> bool:
        """True if this node or any ancestor carries the attribute."""
        return self.closest(lambda n: name in n.attrs) is not None

    def has_ancestor_tag(self, tags) -> bool:
        tags = {tags} if isinstance(tags, str) else set(tags)
        return self.closest(lambda n: n.tag in tags) is not None

    def iter(self) -> Iterator["Node"]:
        """Depth-first over every node including self."""
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def elements(self, tag: str | None = None) -> Iterator["Node"]:
        for n in self.iter():
            if n.is_element and (tag is None or n.tag == tag):
                yield n

    def find_all(self, tag: str | None = None, cls: str | None = None,
                 attr: str | None = None) -> list["Node"]:
        out = []
        for n in self.elements(tag):
            if cls is not None and not n.has_class(cls):
                continue
            if attr is not None and attr not in n.attrs:
                continue
            out.append(n)
        return out

    def by_id(self, id_: str) -> "Node | None":
        for n in self.elements():
            if n.attrs.get("id") == id_:
                return n
        return None

    # -- text -------------------------------------------------------------

    def text_nodes(self) -> Iterator["Node"]:
        """Rendered text nodes only: nothing under <script>, <style>, etc."""
        stack = [self]
        while stack:
            n = stack.pop()
            if n.is_text:
                yield n
                continue
            if n.tag in NON_RENDERED:
                continue
            stack.extend(reversed(n.children))

    def rendered_text(self) -> str:
        """What a reader sees, whitespace-collapsed.

        Block boundaries become a space so `<td>one</td><td>two</td>` reads
        "one two", not "onetwo"; inline boundaries do not, so
        `<strong>$1</strong>,234` stays "$1,234".
        """
        return _WS.sub(" ", "".join(self._render())).strip()

    def _render(self) -> Iterator[str]:
        if self.is_text:
            yield self.text
            return
        if self.tag in NON_RENDERED:
            return
        block = self.tag in BLOCK_ELEMENTS
        if block:
            yield " "
        for c in self.children:
            yield from c._render()
        if block:
            yield " "

    def raw_text(self) -> str:
        """All text including scripts - for the structural checks only."""
        return "".join(n.text for n in self.iter() if n.is_text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.is_text:
            return f"Text({self.text[:40]!r})"
        a = " ".join(f'{k}="{v}"' for k, v in self.attrs.items())
        return f"<{self.tag}{' ' + a if a else ''}> line {self.line}"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(DOCUMENT)
        self.stack = [self.root]

    def _add(self, node: Node) -> None:
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, line=self.getpos()[0])
        self._add(node)
        if tag not in VOID_ELEMENTS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, line=self.getpos()[0])
        self._add(node)

    def handle_endtag(self, tag):
        # Pop to the nearest matching open tag; a stray close tag is ignored
        # here and counted by tag_counts() instead.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not data:
            return
        self._add(Node(TEXT, text=data, line=self.getpos()[0]))

    def handle_comment(self, data):
        pass


def parse(html: str) -> Node:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


class _Counter(HTMLParser):
    def __init__(self, tags: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = tags
        self.opened: dict[str, int] = {t: 0 for t in tags}
        self.closed: dict[str, int] = {t: 0 for t in tags}

    def handle_starttag(self, tag, attrs):
        if tag in self.tags:
            self.opened[tag] += 1

    def handle_startendtag(self, tag, attrs):
        # <div/> is not valid HTML for a non-void tag but browsers treat it as
        # an open tag; count it as opened so the imbalance is reported.
        if tag in self.tags:
            self.opened[tag] += 1

    def handle_endtag(self, tag):
        if tag in self.tags:
            self.closed[tag] += 1


def tag_counts(html: str, tags) -> dict[str, tuple[int, int]]:
    """{tag: (opened, closed)} from a raw pass, so nothing is auto-repaired."""
    c = _Counter(frozenset(tags))
    c.feed(html)
    c.close()
    return {t: (c.opened[t], c.closed[t]) for t in sorted(c.opened)}
