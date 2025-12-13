// editor_bundle.js – minimal contentEditable editor with QWebChannel bridge
// for StoryArkivist. Designed to match ui/widgets/rich_text_editor.py.

(function () {
  "use strict";

  let editorDiv = null;

  let _docConfig = {
    docType: "scratch",
    docId: "experimental",
    versionId: 1,
    markdown: "",
    worldIndex: [],
    candidateIndex: [],
    prefs: {}
  };

  // worldIndex: [{ worldItemId, title, aliases:[{id, value, case_mode}], ... }, ...]
  let _worldAliasIndex = [];

  let _hoverLinkTimer = null;
  let _hoverLinkState = null;  // { worldItemId, aliasId, text }

  let _ctrlDown = false;
  let _linkModifierDown = false; // true when Ctrl/Cmd is held

  // ---------------------------------------------------------------------------
  // Helpers: alias index + linkifying
  // ---------------------------------------------------------------------------

  function buildWorldAliasIndex(worldIndex, candidateIndex) {
    const byAlias = [];
    if (!Array.isArray(worldIndex)) worldIndex = [];
    if (!Array.isArray(candidateIndex)) candidateIndex = [];

    // Real world items
    for (const wi of worldIndex) {
      const wid = wi.id;
      const aliases = wi.aliases || [];
      for (const al of aliases) {
        if (!al || !al.alias) continue;
        byAlias.push({
          kind: "world",                    // real world item alias
          worldItemId: wid,
          aliasId: al.id || null,
          candidateId: null,
          alias: al.alias,
          caseMode: al.case_mode || "case-insensitive"
        });
      }
    }

    // Candidates (scope-specific)
    for (const cand of candidateIndex) {
      if (!cand || !cand.candidate) continue;
      byAlias.push({
        kind: "candidate",
        worldItemId: cand.link_world_id || null,  // may be 0/NULL if not yet linked
        aliasId: null,
        candidateId: cand.id || null,
        alias: cand.candidate,
        caseMode: "case-insensitive"
      });
    }

    byAlias.sort((a, b) => b.alias.length - a.alias.length);
    return byAlias;
  }

  function autoLinkTextWithAliases(html, aliasIndex) {
    if (!html || !aliasIndex || aliasIndex.length === 0) return html;

    let text = html; // in this stub, HTML is simple

    for (const entry of aliasIndex) {
      const surface = entry.alias;
      if (!surface) continue;

      const escaped = surface.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const patt =
        entry.caseMode === "case-sensitive"
          ? new RegExp(`\\b${escaped}\\b`, "g")
          : new RegExp(`\\b${escaped}\\b`, "gi");

      text = text.replace(patt, match => {
        const classes = ["wikilink"];
        if (entry.kind === "candidate") {
          classes.push("wikilink-candidate");
        }
        const attrs = [
          `data-kind="${entry.kind || "world"}"`,
          `data-world-item-id="${String(entry.worldItemId || "")}"`,
          `data-alias-id="${entry.aliasId != null ? String(entry.aliasId) : ""}"`,
          `data-candidate-id="${entry.candidateId != null ? String(entry.candidateId) : ""}"`
        ].join(" ");
        return `<span class="${classes.join(" ")}" ${attrs}>${match}</span>`;
      });
    }

    return text;
  }

  function isAliasTextValidForSpan(spanEl, text) {
    if (!_worldAliasIndex || _worldAliasIndex.length === 0) {
      // No alias index means we can't validate; leave links alone.
      console.warn("[RichEditor] No alias index available.");
      return true;
    }

    const kindAttr = spanEl.getAttribute("data-kind") || "world";
    const widAttr = spanEl.getAttribute("data-world-item-id") || "";
    const aliasIdAttr = spanEl.getAttribute("data-alias-id") || "";
    const candidateIdAttr = spanEl.getAttribute("data-candidate-id") || "";

    const normalizedText = (text || "").trim();
    if (!normalizedText) {
      console.warn("[RichEditor] Empty text in wikilink span");
      return false;
    }

    console.debug("[RichEditor] validate span text", 
      kindAttr,
      widAttr,
      aliasIdAttr,
      candidateIdAttr,
      normalizedText,
    );

    for (const entry of _worldAliasIndex) {
      const entryWid = String(entry.worldItemId || "");
      const entryAliasId =
        entry.aliasId != null ? String(entry.aliasId) : "";
      const entryCandId =
        entry.candidateId != null ? String(entry.candidateId) : "";

      // Check target id matches
      if (kindAttr === "candidate") {
        if (entryCandId !== candidateIdAttr) continue;
      } else {
        if (entryWid !== widAttr) continue;
        if (aliasIdAttr && entryAliasId !== aliasIdAttr) continue;
      }

      const aliasText = entry.alias || "";
      if (!aliasText) continue;

      console.debug("[RichEditor] comparing alias vs span text", 
        aliasText,
        normalizedText,
        "caseMode:", entry.caseMode,
      );

      if (entry.caseMode === "case-sensitive") {
        if (aliasText === normalizedText) {
          console.debug("[RichEditor] alias match (case-sensitive)");
          return true;
        }
      } else {
        if (aliasText.toLowerCase() === normalizedText.toLowerCase()) {
          console.debug("[RichEditor] alias match (case-insensitive)");
          return true;
        }
      }
    }

    console.debug(
      "[RichEditor] no matching alias for span text; will drop link"
    );
    return false;
  }

  function cleanupStaleWikilinks() {
    if (!editorDiv || !_worldAliasIndex || _worldAliasIndex.length === 0) {
      return;
    }

    const spans = editorDiv.querySelectorAll("span.wikilink");
    spans.forEach((spanEl) => {
      const text = spanEl.textContent || "";
      if (!isAliasTextValidForSpan(spanEl, text)) {
        const parent = spanEl.parentNode;
        if (!parent) return;

        const children = Array.from(spanEl.childNodes);
        let refNode = spanEl;
        for (const child of children) {
          parent.insertBefore(child, refNode);
          refNode = child;
        }
        parent.removeChild(spanEl);
      }
    });
  }

  function updateWorldIndex(payload) {
    if (!payload || !Array.isArray(payload.worldIndex)) {
      console.warn("[RichEditor] updateWorldIndex called with bad payload", payload);
      return;
    }
    _docConfig.worldIndex = payload.worldIndex;
    _worldAliasIndex = buildWorldAliasIndex(_docConfig.worldIndex);
    console.info("[RichEditor] updateWorldIndex → aliasIndex size", _worldAliasIndex.length);

    // OPTIONAL: relink whole document here (see below).
    // For now I'd leave it off until you’re sure caret handling is good.
    // relinkWholeDocument();
  }

  // This is optional and you may want to comment it out first while testing:
  function relinkWholeDocument() {
    if (!editorDiv) return;
    // 1. Capture current HTML
    const originalHtml = editorDiv.innerHTML;

    // 2. Strip existing wikilink spans back to plain text
    const stripped = originalHtml.replace(
      /<span\b[^>]*class="wikilink"[^>]*>(.*?)<\/span>/gi,
      "$1"
    );

    // 3. Run autoLinkTextWithAliases over the stripped HTML
    const relinked = autoLinkTextWithAliases(stripped, _worldAliasIndex);

    // 4. TODO: capture + restore selection; for now we accept that caret may jump
    editorDiv.innerHTML = relinked;
  }

  // ---------------------------------------------------------------------------
  // Helpers: naive Markdown <-> HTML
  // ---------------------------------------------------------------------------

  function naiveMarkdownToHtml(md) {
    if (!md) return "";
    const label = "[markdownToHtml]";
    console.debug(`${label} step0: raw markdown\n`, md);

    let esc = String(md)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    console.debug(`${label} step1: escaped entities\n`, esc);

    // Simple symmetric mapping: every newline becomes a <br>.
    // No paragraph splitting — this preserves arbitrarily long runs of blank lines.
    esc = esc.replace(/\n/g, "<br>");
    console.debug(`${label} step2: after newline→<br>\n`, esc);

    // Wrap in a single <p> so we have a block container, but it
    // does *not* encode paragraph structure – it’s just a wrapper.
    const html = `<p>${esc}</p>`;
    console.debug(`${label} step3: final html\n`, html);
    return html;
  }

  function htmlToMarkdown(html) {
    if (!html) return "";

    const container = document.createElement("div");
    container.innerHTML = html;

    const pieces = [];

    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        // Plain text node
        pieces.push(node.nodeValue);
        return;
      }

      if (node.nodeType !== Node.ELEMENT_NODE) {
        return;
      }

      const tag = node.tagName.toLowerCase();

      if (tag === "br") {
        // Soft/hard line break – always a newline in our markdown
        pieces.push("\n");
        return;
      }

      if (tag === "span") {
        // Flatten spans (wikilinks etc): just recurse into children
        node.childNodes.forEach(walk);
        return;
      }

      if (tag === "p" || tag === "div") {
        // Paragraph / block: recurse into children, then maybe add a newline.
        const lastChild = node.lastChild;
        const lastIsBr =
          lastChild &&
          lastChild.nodeType === Node.ELEMENT_NODE &&
          lastChild.tagName.toLowerCase() === "br";

        // Process children
        node.childNodes.forEach(walk);

        // If the last child is a <br>, that <br> already produced a newline,
        // and in "blank line" cases we get:
        //   <p>Markus<br></p><p><br></p><p>asd</p>
        // 1st <p>: "Markus\n"
        // 2nd <p>: "\n"
        // so between them we end up with exactly "\n\n" (one blank line) already.
        //
        // If the last child is *not* a <br>, we add a newline to mark the end
        // of this block.
        if (!lastIsBr) {
          pieces.push("\n");
        }
        return;
      }

      // Any other element: just recurse, ignore the tag itself
      node.childNodes.forEach(walk);
    }

    container.childNodes.forEach(walk);

    let md = pieces.join("");

    // Normalise newlines and trim trailing spaces (but do NOT collapse \n runs)
    md = md.replace(/\r\n/g, "\n");
    md = md.replace(/[ \t]+\n/g, "\n");
    md = md.replace(/[ \t]+$/g, "");

    console.debug("[htmlToMarkdown] result:\n", JSON.stringify(md));
    return md;
  }

  // ---------------------------------------------------------------------------
  // QWebChannel bridge utilities
  // ---------------------------------------------------------------------------

  window.richTextBridge = null;

  function callBridge(method, payload) {
    if (!window.richTextBridge) {
      // fine: editor can still be used visually
      console.debug("[RichEditor] bridge not ready for", method);
      return;
    }
    const obj = window.richTextBridge;
    try {
      if (payload === undefined) {
        if (typeof obj[method] === "function") {
          obj[method]();
        } else {
          console.warn("[RichEditor] bridge method missing:", method);
        }
      } else {
        if (typeof obj[method] === "function") {
          obj[method](payload);
        } else {
          console.warn("[RichEditor] bridge method missing:", method);
        }
      }
    } catch (err) {
      console.error("[RichEditor] Error calling bridge method", method, err);
    }
  }

  function setupWebChannel(callback) {
    if (window.qt && window.qt.webChannelTransport) {
      // Normal QWebChannel path
      // qwebchannel.js is loaded from qrc by the HTML shell
      // eslint-disable-next-line no-undef
      new QWebChannel(window.qt.webChannelTransport, function (channel) {
        window.richTextBridge = channel.objects.richTextBridge;
        console.info("[RichEditor] QWebChannel connected, bridge ready");
        if (callback) callback();
      });
    } else {
      console.warn("[RichEditor] qt.webChannelTransport missing; running without host bridge");
      if (callback) callback();
    }
  }

  // ---------------------------------------------------------------------------
  // Activity + docChanged
  // ---------------------------------------------------------------------------

  let _docChangedTimer = null;
  let _docChangedPendingDirty = true;

  function notifyDocChangedDebounced(dirty) {
    // default: mark dirty
    if (typeof dirty === "undefined") {
      dirty = true;
    }
    _docChangedPendingDirty = !!dirty;

    if (_docChangedTimer) {
      clearTimeout(_docChangedTimer);
    }
    _docChangedTimer = setTimeout(() => {
      _docChangedTimer = null;
      const payload = {
        docId: _docConfig.docId,
        versionId: _docConfig.versionId,
        dirty: _docChangedPendingDirty
      };
      callBridge("onDocChanged", payload);
    }, 250);
  }

  function sendActivity(kind) {
    callBridge("activityPing", kind);
  }

  // ---------------------------------------------------------------------------
  // Event handlers
  // ---------------------------------------------------------------------------

  function handleFocusGained(_ev) {
    console.info("[RichEditor] focusGained");
    callBridge("focusGained");
    sendActivity("focus");
  }

  function handleFocusLost(_ev) {
    console.info("[RichEditor] focusLost");
    callBridge("focusLost");
    sendActivity("blur");
  }

  function updateCtrlHighlight() {
    if (!editorDiv) return;
    const prefs = _docConfig.prefs || {};
    if (!prefs.highlightLinksWhileCtrl) {
      editorDiv.classList.remove("ctrl-links-active");
      return;
    }
    if (_linkModifierDown) {
      editorDiv.classList.add("ctrl-links-active");
    } else {
      editorDiv.classList.remove("ctrl-links-active");
    }
  }

  // Keep _linkModifierDown in sync with the current event's modifier state.
  function syncModifierFromEvent(ev) {
    const mod = !!(ev.ctrlKey || ev.metaKey);
    if (mod !== _linkModifierDown) {
      _linkModifierDown = mod;
      updateCtrlHighlight();
    }
  }

  function handleKeyDown(ev) {
    syncModifierFromEvent(ev);

    // We let Qt's QShortcut(QKeySequence.Save, ...) handle Ctrl+S.
    // Here we just track typing activity; actual docChanged is driven by handleInput.
    sendActivity("typing");
  }

  function handleKeyUp(ev) {
    // Some engines are flaky about keyup for modifiers; this will still run
    // for any other key and keep us in sync.
    syncModifierFromEvent(ev);
  }

  function dropLinkIfTypingInside() {
    if (!editorDiv) return;

    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount === 0) return;

    const range = sel.getRangeAt(0);
    const caretNode = range.startContainer;
    const caretOffset = range.startOffset;
    if (!caretNode) return;

    // Walk up from the caret to see if we are inside a wikilink span
    let el = (caretNode.nodeType === Node.ELEMENT_NODE)
      ? caretNode
      : caretNode.parentNode;

    let linkEl = null;
    while (el && el !== editorDiv) {
      if (
        el.nodeType === Node.ELEMENT_NODE &&
        el.tagName === "SPAN" &&
        el.classList.contains("wikilink")
      ) {
        linkEl = el;
        break;
      }
      el = el.parentNode;
    }

    if (!linkEl) {
      return; // not inside a wikilink
    }

    // If we're editing at the edges of the link text, try to keep the
    // alias core linked and move newly-typed text outside the span.
    if (
      linkEl.childNodes.length === 1 &&
      linkEl.firstChild.nodeType === Node.TEXT_NODE &&
      caretNode === linkEl.firstChild
    ) {
      const text = linkEl.firstChild.textContent || "";
      const len = text.length;

      if (caretOffset <= 0 || caretOffset >= len) {
        const handled = maybeSplitEdgeTypingOutOfLink(
          linkEl,
          caretNode,
          caretOffset
        );
        if (handled) {
          // We successfully split prefix/suffix out of the span and
          // restored the caret; nothing else to do.
          return;
        }
        // If we couldn't split (no alias entry, etc.), fall through
        // to the generic "unwrap the whole link" behavior below.
      }
    }

    const parent = linkEl.parentNode;
    if (!parent) return;

    const children = Array.from(linkEl.childNodes);

    // Unwrap the contents of the span into its parent
    let refNode = linkEl;
    for (const child of children) {
      parent.insertBefore(child, refNode);
      refNode = child;
    }
    parent.removeChild(linkEl);

    const sel2 = window.getSelection && window.getSelection();
    if (!sel2) return;

    const newRange = document.createRange();

    let focusNode = caretNode;
    let focusOffset = caretOffset;

    // If the caret was on the span element itself, move into its first child
    if (focusNode === linkEl && children.length > 0) {
      const first = children[0];
      if (first.nodeType === Node.TEXT_NODE) {
        focusNode = first;
        focusOffset = 0;
      } else if (first.firstChild && first.firstChild.nodeType === Node.TEXT_NODE) {
        focusNode = first.firstChild;
        focusOffset = 0;
      }
    }

    // If the original caret node is no longer attached or not a text node,
    // fall back to "end of the unwrapped content" behavior.
    if (!editorDiv.contains(focusNode) || focusNode.nodeType !== Node.TEXT_NODE) {
      if (children.length > 0) {
        const last = children[children.length - 1];
        if (last.nodeType === Node.TEXT_NODE) {
          focusNode = last;
          focusOffset = (last.textContent || "").length;
        } else if (last.childNodes.length > 0) {
          const innerLast = last.childNodes[last.childNodes.length - 1];
          if (innerLast.nodeType === Node.TEXT_NODE) {
            focusNode = innerLast;
            focusOffset = (innerLast.textContent || "").length;
          } else {
            focusNode = last;
            focusOffset = last.childNodes.length;
          }
        } else {
          focusNode = last;
          focusOffset = 0;
        }
      } else {
        // Span had no children; put caret at its former position in the parent
        focusNode = parent;
        let idx = Array.prototype.indexOf.call(parent.childNodes, refNode);
        if (idx < 0) {
          idx = parent.childNodes.length;
        }
        focusOffset = idx;
      }
    } else {
      // Clamp offset to the new text length
      const len = focusNode.textContent ? focusNode.textContent.length : 0;
      if (focusOffset > len) {
        focusOffset = len;
      }
    }

    newRange.setStart(focusNode, focusOffset);
    newRange.collapse(true);
    sel2.removeAllRanges();
    sel2.addRange(newRange);
  }

  function handleInput(_ev) {
    // If the user is typing inside a wikilink, drop the link wrapper
    dropLinkIfTypingInside();
    notifyDocChangedDebounced(true);
  }

  function handleScroll(_ev) {
    sendActivity("scrolling");
  }

  function handleMouseDown(ev) {
    if (!editorDiv) return;

    const prefs = _docConfig.prefs || {};
    const followMode = prefs.linkFollowMode || "ctrlClick";

    const modifierDown = !!(ev.ctrlKey || ev.metaKey);

    // Decide whether this click should follow links at all.
    if (followMode === "ctrlClick") {
      if (!modifierDown || ev.button !== 0) {
        return;
      }
    } else if (followMode === "click") {
      if (ev.button !== 0) {
        return;
      }
    } else {
      // "none" or unknown → never follow links
      return;
    }

    let el = ev.target;
    while (el && el !== editorDiv) {
      if (el.classList && el.classList.contains("wikilink")) {
        ev.preventDefault();
        ev.stopPropagation();
        const wid = el.getAttribute("data-world-item-id") || "";
        const aid = el.getAttribute("data-alias-id") || "";
        const cid = el.getAttribute("data-candidate-id") || "";
        const kindAttr = el.getAttribute("data-kind") || "world";
        const text = el.textContent || el.innerText || "";
        callBridge("onLinkInteraction", {
          kind: kindAttr === "candidate" ? "candidate" : "wikilink",
          trigger: "click",
          worldItemId: wid,
          aliasId: aid,
          candidateId: cid,
          text,
          docId: _docConfig.docId,
          versionId: _docConfig.versionId
        });
        return;
      }
      if (el.tagName && el.tagName.toLowerCase() === "a" && el.getAttribute("href")) {
        ev.preventDefault();
        ev.stopPropagation();
        callBridge("onLinkInteraction", {
          kind: "external",
          trigger: "click",
          href: el.getAttribute("href"),
          text: el.textContent || el.innerText || "",
          docId: _docConfig.docId,
          versionId: _docConfig.versionId
        });
        return;
      }
      el = el.parentNode;
    }
  }

  function handleMouseMove(ev) {
    if (!editorDiv) return;

    // Keep modifier/highlight in sync even if keyup was missed
    syncModifierFromEvent(ev);

    // Walk up from the event target to see if we're over a wikilink span
    let el = ev.target;
    let found = null;
    while (el && el !== editorDiv) {
      if (el.classList && el.classList.contains("wikilink")) {
        found = el;
        break;
      }
      el = el.parentNode;
    }

    if (!found) {
      // No wikilink under cursor: cancel pending hover and send hoverEnd if needed
      if (_hoverLinkTimer) {
        clearTimeout(_hoverLinkTimer);
        _hoverLinkTimer = null;
      }
      if (_hoverLinkState) {
        callBridge("onLinkInteraction", {
          kind: "wikilink",
          trigger: "hoverEnd",
          worldItemId: _hoverLinkState.worldItemId,
          aliasId: _hoverLinkState.aliasId,
          text: _hoverLinkState.text,
          docId: _docConfig.docId,
          versionId: _docConfig.versionId
        });
        _hoverLinkState = null;
      }
      return;
    }

    const wid = found.getAttribute("data-world-item-id") || "";
    const aid = found.getAttribute("data-alias-id") || "";
    const text = found.textContent || found.innerText || "";

    const same =
      _hoverLinkState &&
      _hoverLinkState.worldItemId === wid &&
      _hoverLinkState.aliasId === aid &&
      _hoverLinkState.text === text;

    if (same) {
      // Still hovering the same link; nothing to do
      return;
    }

    // New link: cancel any pending hover and send hoverEnd for the previous one
    if (_hoverLinkTimer) {
      clearTimeout(_hoverLinkTimer);
      _hoverLinkTimer = null;
    }
    if (_hoverLinkState) {
      callBridge("onLinkInteraction", {
        kind: "wikilink",
        trigger: "hoverEnd",
        worldItemId: _hoverLinkState.worldItemId,
        aliasId: _hoverLinkState.aliasId,
        text: _hoverLinkState.text,
        docId: _docConfig.docId,
        versionId: _docConfig.versionId
      });
    }

    _hoverLinkState = { worldItemId: wid, aliasId: aid, text };

    // Start a short delay before actually firing hoverStart
    _hoverLinkTimer = setTimeout(() => {
      _hoverLinkTimer = null;
      callBridge("onLinkInteraction", {
        kind: "wikilink",
        trigger: "hoverStart",
        worldItemId: wid,
        aliasId: aid,
        text,
        docId: _docConfig.docId,
        versionId: _docConfig.versionId
      });
    }, 250); // tweak delay to taste
  }

  // ---------------------------------------------------------------------------
  // Public RichEditor API (called from Python)
  // ---------------------------------------------------------------------------

  window.RichEditor = {
    initOnce: function () {
      if (editorDiv) return;
      editorDiv = document.getElementById("editorRoot");
      if (!editorDiv) {
        console.error("[RichEditor] editorRoot div not found");
        return;
      }

      editorDiv.contentEditable = "true";
      editorDiv.spellcheck = true;

      editorDiv.addEventListener("focus", handleFocusGained);
      editorDiv.addEventListener("blur", handleFocusLost);
      editorDiv.addEventListener("keydown", handleKeyDown);
      editorDiv.addEventListener("keyup", handleKeyUp);  
      editorDiv.addEventListener("input", handleInput);
      editorDiv.addEventListener("scroll", handleScroll);
      editorDiv.addEventListener("mousedown", handleMouseDown);
      editorDiv.addEventListener("mousemove", handleMouseMove);
    },

    loadDocument: function (config) {
      if (!editorDiv) {
        this.initOnce();
      }

      if (typeof config === "string") {
        try {
          config = JSON.parse(config);
        } catch (e) {
          console.error("[RichEditor] loadDocument JSON parse error:", e);
          return;
        }
      }

      if (!config || typeof config !== "object") {
        console.error("[RichEditor] loadDocument called with non-object:", config);
        return;
      }

      _docConfig = Object.assign({}, _docConfig, config || {});

      // Apply link display mode to the editor root
      const prefs = _docConfig.prefs || {};
      const mode = prefs.showWikilinks || "full";

      editorDiv.classList.remove("links-full", "links-minimal");
      if (mode === "ctrlReveal") {
        editorDiv.classList.add("links-minimal");
      } else if (mode === "minimal") {
        editorDiv.classList.add("links-minimal");
      } else {
        // default: full chrome
        editorDiv.classList.add("links-full");
      }

      _worldAliasIndex = buildWorldAliasIndex(
        _docConfig.worldIndex || [],
        _docConfig.candidateIndex || []
      );

      // Prefer a stored HTML snapshot when provided (e.g. content_render for chapters).
      // Fallback to markdown → HTML conversion otherwise.
      let rawHtml;
      if (
        _docConfig.html &&
        typeof _docConfig.html === "string" &&
        _docConfig.html.trim() !== ""
      ) {
        rawHtml = _docConfig.html;
        // Keep markdown in sync with the HTML snapshot so host code that relies
        // on markdown (e.g. quick-parse) still sees the latest text.
        _docConfig.markdown = htmlToMarkdown(rawHtml);
      } else {
        rawHtml = naiveMarkdownToHtml(_docConfig.markdown || "");
      }

      console.debug("[RichEditor] Converted HTML:\n", rawHtml);
      const html = autoLinkTextWithAliases(rawHtml, _worldAliasIndex);

      editorDiv.innerHTML = html;
      // Put caret at start for now
      const sel = window.getSelection();
      if (sel && editorDiv.firstChild) {
        const range = document.createRange();
        range.setStart(editorDiv.firstChild, 0);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      // Mark as "changed" so host knows editor has loaded something
      notifyDocChangedDebounced(false);

      console.debug("[RichEditor] worldIndex size", (_docConfig.worldIndex || []).length);
      console.debug("[RichEditor] aliasIndex size", _worldAliasIndex.length, _worldAliasIndex);
      console.debug("[RichEditor] rawHtml before autoLink\n", rawHtml);
      console.debug("[RichEditor] html after autoLink\n", html);
    },

    requestSaveFromHost: function () {
      if (!editorDiv) return;

      // Make sure any links whose text no longer matches a known alias
      // are unwrapped before we snapshot HTML/markdown.
      cleanupStaleWikilinks();

      const html = editorDiv.innerHTML;
      const markdown = htmlToMarkdown(html);

      // Keep our local docConfig in sync so any future loadDocument()
      // uses the latest markdown instead of stale content.
      _docConfig.markdown = markdown;

      const payload = {
        docId: _docConfig.docId,
        versionId: _docConfig.versionId,
        markdown: markdown,
        htmlSnapshot: html
      };
      callBridge("requestSave", payload);
    }
  };

  // ---------------------------------------------------------------------------
  // Boot sequence
  // ---------------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    setupWebChannel(function () {
      window.RichEditor.initOnce();
    });
  });
})();
