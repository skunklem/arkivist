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
  // Find UI (Ctrl+F) - MVP: next/prev within the current editor
  // ---------------------------------------------------------------------------

  let _findBar = null;
  let _findInput = null;
  let _findPrevBtn = null;
  let _findNextBtn = null;
  let _findCloseBtn = null;
  let _findStatus = null;

  let _findCacheDirty = true;
  let _findTextNodes = null;
  let _findCurrentRange = null;
  let _findOverlay = null;

  let _findMatches = [];                 // [{ nodeIdx, pos }]
  let _findMatchIndexByKey = new Map();  // "nodeIdx:pos" -> matchIndex
  let _findMatchesQuery = "";
  let _findMatchesTextVersion = -1;
  let _findCurrentMatchIndex = -1;

  let _findTextVersion = 0;              // increment on any editor input affecting text

  let _findOverlayAll = null;            // dim highlight layer
  let _findOverlayCurrent = null;        // bright highlight layer

  const FIND_ALL_HIGHLIGHT_CAP = 500;    // keep UI responsive for common words
  let _findAllHighlightsCapped = false;

  let _findAnchorRange = null;
  let _findAnchorSeq = 0;
  let _findMoveSeq = 0;

  let _editorPadTopBasePx = null;
  let _findClearancePx = 0;
  let _editorPadBottomBasePx = null;
  let _findBottomFillPx = 0;

  function _getPaddingBottomPx(el) {
    const cs = window.getComputedStyle(el);
    const v = parseFloat(cs.paddingBottom || "0");
    return Number.isFinite(v) ? v : 0;
  }

  function _getPaddingTopPx(el) {
    const cs = window.getComputedStyle(el);
    const v = parseFloat(cs.paddingTop || "0");
    return Number.isFinite(v) ? v : 0;
  }

  function _calcFindClearancePx() {
    if (!_findBar) return 0;
    const r = _findBar.getBoundingClientRect();
    const h = r && r.height ? r.height : 0;
    // A little breathing room (or tighter if neg) so text isn’t tight to the bar
    return Math.ceil(h + -5);
  }

  function _rectsIntersect(a, b) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  }

  function _isCurrentMatchCoveredByFindBar() {
    if (!_findBar || _findBar.style.display === "none") return false;
    if (!_findCurrentRange) return false;

    const barRect = _findBar.getBoundingClientRect();
    const rects = Array.from(_findCurrentRange.getClientRects());
    for (const r of rects) {
      if (_rectsIntersect(r, barRect)) return true;
    }
    return false;
  }

  function _applyFindClearance(enabled) {
    if (!editorDiv) return false;

    if (_editorPadTopBasePx == null) _editorPadTopBasePx = _getPaddingTopPx(editorDiv);
    if (_editorPadBottomBasePx == null) _editorPadBottomBasePx = _getPaddingBottomPx(editorDiv);

    const prevClear = _findClearancePx;
    const prevFill = _findBottomFillPx;

    const desired = enabled ? _calcFindClearancePx() : 0;
    const delta = desired - prevClear;

    _findClearancePx = desired;

    // 1) Always apply top clearance when enabled
    editorDiv.style.paddingTop = `${_editorPadTopBasePx + desired}px`;
    editorDiv.style.scrollPaddingTop = `${desired}px`;

    // 2) Ensure we can scroll "up into" the clearance even for short docs.
    //    We do this by padding-bottom so maxScrollTop >= desired.
    if (enabled) {
      // reset to base bottom first (so our measurement isn't cumulative)
      editorDiv.style.paddingBottom = `${_editorPadBottomBasePx}px`;

    const max0 = Math.max(0, editorDiv.scrollHeight - editorDiv.clientHeight);

    // Guarantee: at least ONE viewport of scroll + clearance.
    // (So even short docs get a scrollbar while Find is open.)
    const pagePx = Math.max(0, editorDiv.clientHeight);
    const minMaxScroll = desired + pagePx;

    const fill = Math.max(0, minMaxScroll - max0);

      _findBottomFillPx = fill;
      editorDiv.style.paddingBottom = `${_editorPadBottomBasePx + fill}px`;
    } else {
      _findBottomFillPx = 0;
      editorDiv.style.paddingBottom = `${_editorPadBottomBasePx}px`;
    }

    // 3) Compensate scrollTop by delta to keep the visible content from "jumping"
    //    when we change padding-top.
    if (delta !== 0) {
      const max = Math.max(0, editorDiv.scrollHeight - editorDiv.clientHeight);
      editorDiv.scrollTop = Math.max(0, Math.min(editorDiv.scrollTop + delta, max));
    }

    return (delta !== 0) || (prevFill !== _findBottomFillPx);
  }

  function _captureFindAnchorFromSelection() {
    if (!editorDiv) return;

    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount === 0) return;

    const r = sel.getRangeAt(0);
    if (!r || !editorDiv.contains(r.startContainer)) return;

    const c = r.cloneRange();
    c.collapse(true);

    _findAnchorRange = c;
    _findAnchorSeq += 1;
  }
  
  function _clearOverlay(ov) {
    if (ov) ov.innerHTML = "";
  }

  function _paintRectsToOverlay(ov, rects, fillRgba, outlineRgba) {
    if (!editorDiv || !ov) return;

    const editorRect = editorDiv.getBoundingClientRect();

    for (const r of rects) {
      if (!r || (r.width === 0 && r.height === 0)) continue;

      const box = document.createElement("div");
      box.style.position = "absolute";
      box.style.left = `${(r.left - editorRect.left) + editorDiv.scrollLeft}px`;
      box.style.top = `${(r.top - editorRect.top) + editorDiv.scrollTop}px`;
      box.style.width = `${r.width}px`;
      box.style.height = `${r.height}px`;
      box.style.borderRadius = "3px";
      box.style.background = fillRgba;

      if (outlineRgba) {
        box.style.outline = `1px solid ${outlineRgba}`;
        box.style.outlineOffset = "-1px";
      }

      ov.appendChild(box);
    }
  }

  function _rangeForMatch(m) {
    const node = _findTextNodes[m.nodeIdx];
    const r = document.createRange();
    r.setStart(node, m.pos);
    r.setEnd(node, m.pos + _findMatchesQuery.length);
    return r;
  }

  function _paintAllFindHighlights() {
    _ensureFindOverlay();
    _clearOverlay(_findOverlayAll);

    const total = _findMatches.length;
    if (total === 0) return;

    // cap: if too many matches, don’t paint all (still count them)
    if (total > FIND_ALL_HIGHLIGHT_CAP) {
      _findAllHighlightsCapped = true;
      return;
    }
    _findAllHighlightsCapped = false;

    // yellow (dim)
    for (const m of _findMatches) {
      const r = _rangeForMatch(m);
      _paintRectsToOverlay(_findOverlayAll, Array.from(r.getClientRects()),
        "rgba(255, 235, 59, 0.34)",   // brighter yellow fill
        "rgba(255, 235, 59, 0.62)"    // yellow outline
      );
    }
  }

  function _paintCurrentFindHighlight(range) {
    _ensureFindOverlay();
    _clearOverlay(_findOverlayCurrent);

    if (!range) return;

    // orange (bright)
    _paintRectsToOverlay(_findOverlayCurrent, Array.from(range.getClientRects()),
      "rgba(255, 140, 0, 0.44)",    // brighter orange fill
      "rgba(255, 140, 0, 0.80)"     // orange outline
    );
  }

  function _ensureFindOverlay() {
    if (_findOverlayAll && _findOverlayCurrent) return;
    if (!editorDiv) return;
    
    // Make editorDiv a positioning context (safe even if already set)
    if (!editorDiv.style.position) editorDiv.style.position = "relative";

    const mk = () => {
      const ov = document.createElement("div");
      ov.style.position = "absolute";
      ov.style.left = "0";
      ov.style.top = "0";
      ov.style.right = "0";
      ov.style.bottom = "0";
      ov.style.pointerEvents = "none";
      ov.style.zIndex = "9998";
      ov.style.overflow = "visible";
      return ov;
    };

    _findOverlayAll = mk();      // dim layer (under)
    _findOverlayCurrent = mk();  // bright layer (over)

    editorDiv.appendChild(_findOverlayAll);
    editorDiv.appendChild(_findOverlayCurrent);
  }

  function clearFindHighlight() {
    _findCurrentRange = null;
    _findCurrentMatchIndex = -1;
    _clearOverlay(_findOverlayCurrent);
    _updateFindStatus();
  }

  function clearAllFindHighlights() {
    clearFindHighlight();
    _clearOverlay(_findOverlayAll);
  }

  function setFindHighlight(range, nodeIdx, pos) {
    _findCurrentRange = range;

    if (nodeIdx == null || pos == null) {
      _paintCurrentFindHighlight(range);
      _updateFindStatus();
      return true;
    }

    let idx = _findMatchIndexByKey.get(_matchKey(nodeIdx, pos));
    if (idx == null) {
      idx = -1;
      for (let i = 0; i < _findMatches.length; i++) {
        const m = _findMatches[i];
        if (m.nodeIdx === nodeIdx && m.pos === pos) { idx = i; break; }
      }
    }

    _findCurrentMatchIndex = idx;
    _paintCurrentFindHighlight(range);
    _updateFindStatus();
    return true;
  }

  function _paintFindOverlay(range) {
    if (!editorDiv) return;
    _ensureFindOverlay();
    if (!_findOverlay) return;

    _findOverlay.innerHTML = "";

    const editorRect = editorDiv.getBoundingClientRect();
    const rects = Array.from(range.getClientRects());

    for (const r of rects) {
      if (!r || (r.width === 0 && r.height === 0)) continue;

      const box = document.createElement("div");
      box.style.position = "absolute";
      box.style.left = `${(r.left - editorRect.left) + editorDiv.scrollLeft}px`;
      box.style.top = `${(r.top - editorRect.top) + editorDiv.scrollTop}px`;
      box.style.width = `${r.width}px`;
      box.style.height = `${r.height}px`;
      box.style.borderRadius = "3px";
      box.style.background = "rgba(255, 220, 80, 0.35)";
      box.style.boxShadow = "0 0 0 1px rgba(255, 220, 80, 0.35) inset";

      _findOverlay.appendChild(box);
    }
  }

  function _ensureFindUI() {
    if (_findBar) return;
    const wrap = document.getElementById("wrapper") || document.body;

    const bar = document.createElement("div");
    bar.style.position = "absolute";
    bar.style.top = "0px";
    bar.style.right = "16px"; // leave room for the scrollbar
    bar.style.zIndex = "9999";
    bar.style.display = "none";
    bar.style.gap = "6px";
    bar.style.alignItems = "center";
    bar.style.padding = "2px 2px";
    bar.style.border = "1px solid rgba(0,0,0,0.25)";
    bar.style.borderRadius = "4px";
    bar.style.background = "rgba(30,30,30,0.92)";
    bar.style.backdropFilter = "blur(6px)";
    bar.style.color = "white";
    bar.style.fontFamily = "system-ui, -apple-system, Segoe UI, sans-serif";
    bar.style.fontSize = "13px";

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Find…";
    input.style.width = "240px";
    input.style.padding = "4px 6px";
    input.style.borderRadius = "6px";
    input.style.border = "1px solid rgba(255,255,255,0.25)";
    input.style.outline = "none";
    input.style.background = "rgba(0,0,0,0.25)";
    input.style.color = "white";

    const mkBtn = (label) => {
      const b = document.createElement("button");
      b.textContent = label;

      // Base (idle) look: effectively “no outline”
      b.style.margin = "0";
      b.style.padding = "2px 6px";
      b.style.borderRadius = "4px";
      b.style.border = "1px solid transparent";
      b.style.background = "transparent";
      b.style.color = "#ddd";
      b.style.cursor = "pointer";
      b.style.lineHeight = "1";
      b.style.transition = "border-color 80ms linear, background 80ms linear";

      const applyHover = (on) => {
        if (on) {
          b.style.borderColor = "rgba(80, 170, 255, 0.95)";   // blue border on hover
          b.style.background = "rgba(80, 170, 255, 0.12)";    // subtle fill (optional)
        } else {
          b.style.borderColor = "transparent";
          b.style.background = "transparent";
        }
      };

      b.addEventListener("mouseenter", () => applyHover(true));
      b.addEventListener("mouseleave", () => applyHover(false));
      b.addEventListener("focus", () => applyHover(true));
      b.addEventListener("blur", () => applyHover(false));

      return b;
    };

    const prevBtn = mkBtn("↑");
    const nextBtn = mkBtn("↓");
    const closeBtn = mkBtn("✕");

    const status = document.createElement("span");
    status.style.opacity = "0.85";
    status.style.marginLeft = "4px";
    status.style.display = "inline-block";
    status.style.minWidth = "88px";         // keeps arrows from moving
    status.style.textAlign = "right";
    status.style.fontVariantNumeric = "tabular-nums";
    status.textContent = "0 of 0";

    bar.appendChild(input);
    bar.appendChild(prevBtn);
    bar.appendChild(nextBtn);
    bar.appendChild(closeBtn);
    bar.appendChild(status);

    wrap.style.position = wrap.style.position || "relative";
    wrap.appendChild(bar);

    _findBar = bar;
    _findInput = input;
    _findPrevBtn = prevBtn;
    _findNextBtn = nextBtn;
    _findCloseBtn = closeBtn;
    _findStatus = status;

    prevBtn.addEventListener("click", () => _findNext(true));
    nextBtn.addEventListener("click", () => _findNext(false));
    closeBtn.addEventListener("click", () => _hideFind());

    input.addEventListener("input", () => {
      const q = input.value;

      if (q.length === 0) {
        _findMatches = [];
        _findMatchIndexByKey = new Map();
        _findMatchesQuery = "";
        _findCurrentMatchIndex = -1;
        _findMatchesTextVersion = -1;

        clearAllFindHighlights();
        if (_findStatus) _findStatus.textContent = "0 of 0";
        return;
      }

      _findNext(false, true);
    });

    _findInput.addEventListener("keydown", (ev) => {
      ev.stopPropagation(); // critical: prevent editor from seeing keys
      if (ev.key === "Escape") { ev.preventDefault(); _hideFind(); return; }
      if (ev.key === "Enter") { ev.preventDefault(); _findNext(ev.shiftKey); return; }
    });
    _findInput.addEventListener("keypress", (ev) => ev.stopPropagation());
    _findInput.addEventListener("keyup", (ev) => ev.stopPropagation());
  }

  function _showFind(prefillText) {
    _ensureFindUI();
    // capture editor selection BEFORE focus() clears it
    _captureFindAnchorFromSelection();

    _findBar.style.display = "flex";

    if (typeof prefillText === "string" && prefillText.length > 0) {
      _findInput.value = prefillText;
      _findCacheDirty = true;
    }

    _findInput.focus();
    _findInput.select();

    requestAnimationFrame(() => {
      const changed = _applyFindClearance(true);

      // Pick the “current” match after clearance exists
      _jumpFindToAnchorOrFirst();

      // Always repaint on show (cheap and avoids edge-state weirdness)
      if (_findMatches.length) _paintAllFindHighlights();
      if (_findCurrentRange) _paintCurrentFindHighlight(_findCurrentRange);
    });
  }

  function _hideFind() {
    if (!_findBar) return;
    _applyFindClearance(false);
    _findBar.style.display = "none";
    _findStatus.textContent = "0 of 0";
    clearAllFindHighlights();   // <- clears yellow + orange
    if (editorDiv) editorDiv.focus();
  }

  function _buildFindTextNodeCache() {
    _findTextNodes = [];
    if (!editorDiv) return;

    const walker = document.createTreeWalker(
      editorDiv,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: (n) => {
          if (!n || !n.nodeValue) return NodeFilter.FILTER_REJECT;
          return n.nodeValue.length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      }
    );

    let n;
    while ((n = walker.nextNode())) {
      _findTextNodes.push(n);
    }
  }

  function _selectFindMatchAt(nodeIdx, pos, qLen) {
    const node = _findTextNodes[nodeIdx];
    if (!node || node.nodeType !== Node.TEXT_NODE) return false;

    const range = document.createRange();
    range.setStart(node, pos);
    range.setEnd(node, pos + qLen);

    setFindHighlight(range, nodeIdx, pos);
    _findMoveSeq = _findAnchorSeq;

    requestAnimationFrame(() => scrollRangeIntoViewIfNeeded(range));

    if (_findInput) _findInput.focus({ preventScroll: true });
    return true;
  }

  function _jumpFindToAnchorOrFirst() {
    const q = (_findInput && _findInput.value ? _findInput.value : "");
    if (q.length === 0) return false;

    const qLower = q.toLowerCase();
    const qLen = q.length;

    _ensureFindMatches(qLower);
    _updateFindStatus();

    if (!_findMatches.length) {
      clearFindHighlight();
      _updateFindStatus();
      return false;
    }

    const a = _findAnchorRange;
    if (a && a.startContainer && a.startContainer.nodeType === Node.TEXT_NODE && editorDiv.contains(a.startContainer)) {
      const nodeIdx = _findTextNodes.indexOf(a.startContainer);
      if (nodeIdx >= 0) {
        const off = a.startOffset;

        // Find the first match that contains the caret, else first match at/after caret, else after node, else first.
        let bestIdx = -1;

        for (let i = 0; i < _findMatches.length; i++) {
          const m = _findMatches[i];
          if (m.nodeIdx !== nodeIdx) continue;
          const start = m.pos;
          const end = m.pos + qLen;
          if (start <= off && off <= end) { bestIdx = i; break; }
        }

        if (bestIdx < 0) {
          for (let i = 0; i < _findMatches.length; i++) {
            const m = _findMatches[i];
            if (m.nodeIdx !== nodeIdx) continue;
            if (m.pos >= off) { bestIdx = i; break; }
          }
        }

        if (bestIdx < 0) {
          for (let i = 0; i < _findMatches.length; i++) {
            const m = _findMatches[i];
            if (m.nodeIdx > nodeIdx) { bestIdx = i; break; }
          }
        }

        if (bestIdx < 0) bestIdx = 0;
        const m = _findMatches[bestIdx];
        return _selectFindMatchAt(m.nodeIdx, m.pos, qLen);
      }
    }

    const m0 = _findMatches[0];
    return _selectFindMatchAt(m0.nodeIdx, m0.pos, qLen);
  }

  function scrollRangeIntoViewIfNeeded(range) {
    if (!editorDiv || !range) return;

    const rect = range.getBoundingClientRect();
    const boxRect = editorDiv.getBoundingClientRect();

    // If rect is empty/invalid, fall back to element scrollIntoView (matches caret logic style)
    if (!rect || (rect.top === 0 && rect.bottom === 0)) {
      let node = range.startContainer;
      if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
      if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest", inline: "nearest" });
      return;
    }

    const pad = 24;

    // If Find is visible, treat the clearance as the “top safe zone”.
    // This makes autoscroll happen only when the match is near/under the bar.
    const findVisible = _findBar && _findBar.style.display !== "none";
    const topPad = findVisible ? Math.max(pad, _findClearancePx + 6) : pad;

    const topLimit = boxRect.top + topPad;
    const botLimit = boxRect.bottom - pad;

    if (rect.top < topLimit) {
      editorDiv.scrollTop -= (topLimit - rect.top);
    } else if (rect.bottom > botLimit) {
      editorDiv.scrollTop += (rect.bottom - botLimit);
    }
  }

  function _findNext(backwards, fromTop) {
    if (!editorDiv) return;
    _ensureFindUI();

    const q = (_findInput && _findInput.value ? _findInput.value : "");
    if (q.length === 0) {
      clearAllFindHighlights();     // or clearFindHighlight() if you prefer
      _findStatus.textContent = "0 of 0";
      return;
    }

    if (_findCacheDirty || !_findTextNodes) {
      _buildFindTextNodeCache();
      _findCacheDirty = false;
    }

    const nodes = _findTextNodes || [];
    if (!nodes.length) {
      _findStatus.textContent = "No matches";
      clearFindHighlight();
      return;
    }

    const qLower = q.toLowerCase();
    _ensureFindMatches(qLower);
    _updateFindStatus();

    // Base range:
    // - if user moved caret since last find, use caret anchor
    // - otherwise use current found match range
    let base = null;
    const useAnchor = (_findAnchorRange && _findAnchorSeq > _findMoveSeq);
    if (!fromTop) {
      if (useAnchor) base = _findAnchorRange;
      else if (_findCurrentRange) base = _findCurrentRange;
    }

    let startNodeIdx = backwards ? (nodes.length - 1) : 0;
    let startOff = backwards ? Number.MAX_SAFE_INTEGER : 0;

    if (!fromTop && base && base.startContainer && base.startContainer.nodeType === Node.TEXT_NODE) {
      const container = backwards ? base.startContainer : base.endContainer;
      const off = backwards ? (base.startOffset - 1) : base.endOffset;

      const idx = nodes.indexOf(container);
      if (idx >= 0) {
        startNodeIdx = idx;
        startOff = off;
      }
    } else if (!fromTop) {
      // fall back to current editor selection if it exists in the editor
      const sel = window.getSelection && window.getSelection();
      if (sel && sel.rangeCount > 0) {
        const r = sel.getRangeAt(0);
        if (r && editorDiv.contains(r.startContainer) && r.startContainer.nodeType === Node.TEXT_NODE) {
          const container = backwards ? r.startContainer : r.endContainer;
          const off = backwards ? (r.startOffset - 1) : r.endOffset;
          const idx = nodes.indexOf(container);
          if (idx >= 0) {
            startNodeIdx = idx;
            startOff = off;
          }
        }
      }
    }

    const trySelect = (nodeIdx, pos) => {
      const node = _findTextNodes[nodeIdx];
      if (!node || node.nodeType !== Node.TEXT_NODE) return false; // prevents Range.setStart crash

      const range = document.createRange();
      range.setStart(node, pos);
      range.setEnd(node, pos + q.length);

      // temp debug log
      const key = _matchKey(nodeIdx, pos);
      const idx = _findMatchIndexByKey.get(key);

      setFindHighlight(range, nodeIdx, pos);      // sets _findCurrentRange internally

      const changed = _applyFindClearance(true);
      if (changed) {
        requestAnimationFrame(() => {
          if (_findMatches.length) _paintAllFindHighlights();
          if (_findCurrentRange) _paintCurrentFindHighlight(_findCurrentRange);
        });
      }

      _findMoveSeq = _findAnchorSeq; // selection now becomes the base unless user moves caret

      requestAnimationFrame(() => scrollRangeIntoViewIfNeeded(range));

      if (_findInput) _findInput.focus({ preventScroll: true });
      return true;
    };

    const findInNodeForward = (node, fromOff) => {
      const t = node.nodeValue || "";
      const hay = t.toLowerCase();
      const off = Math.max(0, fromOff || 0);
      const pos = hay.indexOf(qLower, off);
      return pos >= 0 ? pos : -1;
    };

    const findInNodeBackward = (node, fromOff) => {
      const t = node.nodeValue || "";
      const hay = t.toLowerCase();

      // fromOff is the last index we allow; if < 0, no match in this node
      if (fromOff < 0) return -1;

      const end = Math.min(hay.length, fromOff + 1);
      if (end <= 0) return -1;

      const pos = hay.lastIndexOf(qLower, end - 1);
      return pos >= 0 ? pos : -1;
    };

    let found = false;

    if (backwards) {
      // 1) search current -> beginning
      for (let i = startNodeIdx; i >= 0; i--) {
        const off = (i === startNodeIdx) ? startOff : Number.MAX_SAFE_INTEGER;
        const pos = findInNodeBackward(nodes[i], off);
        if (pos >= 0) { found = trySelect(i, pos); break; }
      }

      // 2) wrap: search end -> (startNodeIdx + 1), EXCLUDING current node to avoid "stuck at 0"
      if (!found) {
        for (let i = nodes.length - 1; i > startNodeIdx; i--) {
          const pos = findInNodeBackward(nodes[i], Number.MAX_SAFE_INTEGER);
          if (pos >= 0) { found = trySelect(i, pos); break; }
        }
      }
    } else {
      // 1) search current -> end
      for (let i = startNodeIdx; i < nodes.length; i++) {
        const off = (i === startNodeIdx) ? startOff : 0;
        const pos = findInNodeForward(nodes[i], off);
        if (pos >= 0) { found = trySelect(i, pos); break; }
      }

      // 2) wrap: search beginning -> (startNodeIdx - 1), EXCLUDING current node
      if (!found) {
        for (let i = 0; i < startNodeIdx; i++) {
          const pos = findInNodeForward(nodes[i], 0);
          if (pos >= 0) { found = trySelect(i, pos); break; }
        }
      }
    }

    if (!found && _findMatches.length > 0) {
      const m = backwards ? _findMatches[_findMatches.length - 1] : _findMatches[0];
      found = trySelect(m.nodeIdx, m.pos);
    }

    if (!found) {
      clearFindHighlight();
      _updateFindStatus(); // will show 0 of N (or 0 of 0)
    }
  }

  function _matchKey(nodeIdx, pos) {
    return `${nodeIdx}:${pos}`;
  }

  function _ensureFindMatches(qLower) {
    if (!editorDiv) return;

    // Empty query: avoid indexOf("", ...) infinite loop
    if (!qLower) {
      _findMatchesQuery = "";
      _findMatchesTextVersion = _findTextVersion;
      _findMatches = [];
      _findMatchIndexByKey = new Map();
      _findAllHighlightsCapped = false;
      _clearOverlay(_findOverlayAll);
      return;
    }

    const needRebuild =
      (qLower !== _findMatchesQuery) ||
      (_findMatchesTextVersion !== _findTextVersion) ||
      _findCacheDirty;

    if (!needRebuild) return;

    _findMatchesQuery = qLower;
    _findMatchesTextVersion = _findTextVersion;
    _findMatches = [];
    _findMatchIndexByKey = new Map();
    _findAllHighlightsCapped = false;

    // Make sure text nodes cache exists (your code already does this in _findNext, but safe here)
    if (_findCacheDirty || !_findTextNodes) {
      _buildFindTextNodeCache();
      _findCacheDirty = false;
    }

    const nodes = _findTextNodes || [];
    const qLen = qLower.length;

    for (let nodeIdx = 0; nodeIdx < nodes.length; nodeIdx++) {
      const t = nodes[nodeIdx].nodeValue || "";
      const hay = t.toLowerCase();

      let start = 0;
      while (true) {
        const pos = hay.indexOf(qLower, start);
        if (pos < 0) break;

        const idx = _findMatches.length;
        _findMatches.push({ nodeIdx, pos });
        _findMatchIndexByKey.set(_matchKey(nodeIdx, pos), idx);

        // advance non-overlapping
        start = pos + qLen;
      }
    }

    // repaint dim-all when query changes / doc changes (but cap for performance)
    _paintAllFindHighlights();
  }

  function _updateFindStatus() {
    if (!_findStatus) return;

    const total = _findMatches.length;
    const cur = (_findCurrentMatchIndex >= 0) ? (_findCurrentMatchIndex + 1) : 0;

    _findStatus.textContent = `${cur} of ${total}`;

    // let s = `${cur} of ${total}`;
    // if (_findAllHighlightsCapped) s += " (highlights capped)";
    // _findStatus.textContent = s;
  }

  let _findEditRefreshTimer = null;

  function _isFindVisible() {
    return _findBar && _findBar.style.display !== "none";
  }

  function _scheduleFindRefreshAfterEdit() {
    if (!_isFindVisible()) return;
    if (!_findInput) return;

    const q = _findInput.value;
    if (!q || q.length === 0) return;

    if (_findEditRefreshTimer) clearTimeout(_findEditRefreshTimer);
    _findEditRefreshTimer = setTimeout(() => {
      _findEditRefreshTimer = null;

      // Force cache rebuild from latest DOM
      _findCacheDirty = true;

      const qLower = q.toLowerCase();
      _ensureFindMatches(qLower);
      _updateFindStatus();

      if (_findMatches.length === 0) {
        clearFindHighlight();
        _updateFindStatus();
        return;
      }

      // Keep the same "match number" if possible
      let idx = _findCurrentMatchIndex;
      if (idx < 0) idx = 0;
      if (idx >= _findMatches.length) idx = _findMatches.length - 1;

      const m = _findMatches[idx];
      const node = _findTextNodes && _findTextNodes[m.nodeIdx];
      if (!node || node.nodeType !== Node.TEXT_NODE) return;

      const range = document.createRange();
      range.setStart(node, m.pos);
      range.setEnd(node, m.pos + q.length);

      // repaint + update status, but no scroll/focus
      setFindHighlight(range, m.nodeIdx, m.pos);
    }, 60);
  }

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

  function updateModifierDownClass() {
    if (!editorDiv) return;
    if (_linkModifierDown) {
      editorDiv.classList.add("modifier-down");
    } else {
      editorDiv.classList.remove("modifier-down");
    }
  }

  // Keep _linkModifierDown in sync with the current event's modifier state.
  function syncModifierFromEvent(ev) {
    const mod = !!(ev.ctrlKey || ev.metaKey);
    if (mod !== _linkModifierDown) {
      _linkModifierDown = mod;
      updateCtrlHighlight();
      updateModifierDownClass();
    }
  }

  function _isAllSpaces(s) {
    // Treat normal space + NBSP as “spaces”
    return s.length > 0 && /^[ \u00A0]+$/.test(s);
  }

  function _prefillFromSelection(raw) {
    if (typeof raw !== "string" || raw.length === 0) return "";

    // If selection is ONLY spaces, keep it exactly (user may want to find indenting / alignment)
    if (_isAllSpaces(raw)) return raw;

    // Otherwise, strip ONLY edge spaces (not internal spaces)
    return raw.replace(/^[ \u00A0]+|[ \u00A0]+$/g, "");
  }

  function handleKeyDown(ev) {
    syncModifierFromEvent(ev);

    // Ctrl/Cmd+F: open Find bar (prefill with selection if present)
    if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && (ev.key === "f" || ev.key === "F")) {
      ev.preventDefault();
      ev.stopPropagation();

      const sel = window.getSelection && window.getSelection();
      const rawSelected = sel ? (sel.toString() || "") : "";
      const selected = _prefillFromSelection(rawSelected);
      _showFind(selected);
      return;
    }

    // Esc closes Find if it's open
    if (ev.key === "Escape" && _findBar && _findBar.style.display !== "none") {
      ev.preventDefault();
      ev.stopPropagation();
      _hideFind();
      return;
    }

    // Normal typing activity
    sendActivity("typing");
  }

  function handleKeyUp(ev) {
    // Some engines are flaky about keyup for modifiers; this will still run
    // for any other key and keep us in sync.
    syncModifierFromEvent(ev);
    _captureFindAnchorFromSelection();
  }

  function expectedAliasForSpan(spanEl) {
    if (!_worldAliasIndex || _worldAliasIndex.length === 0) return null;

    const kindAttr = spanEl.getAttribute("data-kind") || "world";
    const widAttr = spanEl.getAttribute("data-world-item-id") || "";
    const aliasIdAttr = spanEl.getAttribute("data-alias-id") || "";
    const candIdAttr = spanEl.getAttribute("data-candidate-id") || "";

    for (const entry of _worldAliasIndex) {
      const entryWid = String(entry.worldItemId || "");
      const entryAliasId = entry.aliasId != null ? String(entry.aliasId) : "";
      const entryCandId = entry.candidateId != null ? String(entry.candidateId) : "";

      if (kindAttr === "candidate") {
        if (entryCandId !== candIdAttr) continue;
      } else {
        if (entryWid !== widAttr) continue;
        if (entryAliasId !== aliasIdAttr) continue;
      }
      return entry.alias || null;
    }
    return null;
  }

  function setCaret(node, offset) {
    const sel = window.getSelection && window.getSelection();
    if (!sel) return;
    const r = document.createRange();
    r.setStart(node, Math.max(0, offset));
    r.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r);
  }

  function maybeSplitEdgeTypingOutOfLink(linkEl, caretNode, caretOffset) {
    // Goal: keep the linked alias intact when typing at the *edges* of a wikilink,
    // moving any extra prefix/suffix outside the span.
    if (!linkEl || !caretNode || caretNode.nodeType !== Node.TEXT_NODE) return false;
    const parent = linkEl.parentNode;
    if (!parent) return false;

    const expected = expectedAliasForSpan(linkEl);
    if (!expected) return false;

    const full = caretNode.textContent || "";
    if (full === expected) return false;

    // Suffix typed at end: "John" -> "Johns"
    if (full.startsWith(expected)) {
      const suffix = full.slice(expected.length);
      if (suffix.length > 0) {
        caretNode.textContent = expected;
        const suffixNode = document.createTextNode(suffix);
        parent.insertBefore(suffixNode, linkEl.nextSibling);
        // put caret in the suffix, at end (feels like "typing continues outside the link")
        setCaret(suffixNode, suffix.length);
        return true;
      }
    }

    // Prefix typed at start: "John" -> "xJohn"
    if (full.endsWith(expected)) {
      const prefix = full.slice(0, full.length - expected.length);
      if (prefix.length > 0) {
        caretNode.textContent = expected;
        const prefixNode = document.createTextNode(prefix);
        parent.insertBefore(prefixNode, linkEl);
        // caret at end of prefix
        setCaret(prefixNode, prefix.length);
        return true;
      }
    }

    return false;
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

  function scrollCaretIntoViewIfNeeded() {
    if (!editorDiv) return;

    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount === 0) return;

    const r = sel.getRangeAt(0).cloneRange();
    r.collapse(true);

    const caretRect = r.getBoundingClientRect();
    const boxRect = editorDiv.getBoundingClientRect();

    // If rect is empty/invalid, fall back to element scrollIntoView
    if (!caretRect || (caretRect.top === 0 && caretRect.bottom === 0)) {
      let node = r.startContainer;
      if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
      if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest", inline: "nearest" });
      return;
    }

    const pad = 24;
    const topLimit = boxRect.top + pad;
    const botLimit = boxRect.bottom - pad;

    if (caretRect.top < topLimit) {
      editorDiv.scrollTop -= (topLimit - caretRect.top);
    } else if (caretRect.bottom > botLimit) {
      editorDiv.scrollTop += (caretRect.bottom - botLimit);
    }
  }

  function handleInput(ev) {
    // If the user is typing inside a wikilink, drop the link wrapper
    dropLinkIfTypingInside();
    _findCacheDirty = true;
    _findTextVersion += 1;
    _scheduleFindRefreshAfterEdit();
    notifyDocChangedDebounced(true);

    if (ev && (ev.inputType === "historyUndo" || ev.inputType === "historyRedo")) {
      requestAnimationFrame(scrollCaretIntoViewIfNeeded);
    }
  }

  function handleScroll(_ev) {
    sendActivity("scrolling");
    if (_findBar && _findBar.style.display !== "none" && _findCurrentRange) {
      requestAnimationFrame(() => _paintCurrentFindHighlight(_findCurrentRange));
    }
  }

  function handleMouseDown(ev) {
    if (!editorDiv) return;

    const prefs = _docConfig.prefs || {};
    const followMode = prefs.linkFollowMode || "ctrlClick";

    const modifierDown = !!(ev.ctrlKey || ev.metaKey);

    // If the user clicks on "empty" space inside the editor root, Chromium can fail
    // to move the caret. Explicitly place it based on click point, fallback to end.
    if (ev.button === 0 && ev.target === editorDiv) {
      ev.preventDefault();
      editorDiv.focus();

      const sel = window.getSelection && window.getSelection();
      if (sel) {
        let range = null;

        if (document.caretRangeFromPoint) {
          range = document.caretRangeFromPoint(ev.clientX, ev.clientY);
        } else if (document.caretPositionFromPoint) {
          const pos = document.caretPositionFromPoint(ev.clientX, ev.clientY);
          if (pos) {
            range = document.createRange();
            range.setStart(pos.offsetNode, pos.offset);
            range.collapse(true);
          }
        }

        if (!range || !editorDiv.contains(range.startContainer)) {
          range = document.createRange();
          range.selectNodeContents(editorDiv);
          range.collapse(false);
        }

        sel.removeAllRanges();
        sel.addRange(range);
      }
    }

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
      editorDiv.addEventListener("mouseup", _captureFindAnchorFromSelection);
      editorDiv.addEventListener("mousemove", handleMouseMove);

      _ensureFindUI();
      window.addEventListener("resize", () => {
        if (_findBar && _findBar.style.display !== "none") {
          requestAnimationFrame(() => {
            const changed = _applyFindClearance(true);
            if (changed) {
              if (_findMatches.length) _paintAllFindHighlights();
              if (_findCurrentRange) _paintCurrentFindHighlight(_findCurrentRange);
            }
          });
        }
      });
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

      updateModifierDownClass();

      const followMode = prefs.linkFollowMode || "ctrlClick";
      editorDiv.classList.remove("follow-click", "follow-ctrlClick", "follow-none");
      if (followMode === "click") {
        editorDiv.classList.add("follow-click");
      } else if (followMode === "ctrlClick") {
        editorDiv.classList.add("follow-ctrlClick");
      } else {
        editorDiv.classList.add("follow-none");
      }

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
