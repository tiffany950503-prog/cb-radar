(function () {
  "use strict";

  const state = { rows: [], history: [], sortKey: "premium", sortAsc: true };

  const fmtPct = (v) => (v == null ? "—" : (v * 100).toFixed(2) + "%");
  const fmtNum = (v, d) => (v == null ? "—" : v.toFixed(d));

  const NULL_REASON_TEXT = {
    not_traded: "今天沒有成交",
    no_stock_price: "沒有股價資料",
    no_conv_price: "缺轉換價",
    exchangeable: "交換債，溢價率不適用",
  };
  const SIGNAL_LABEL = { candidate: "訊號", suspect: "⚠ 資料可疑", none: "" };

  function applyFilters(rows) {
    const onlyTraded = document.getElementById("f-traded").checked;
    const onlyStartable = document.getElementById("f-startable").checked;
    const noExchangeable = document.getElementById("f-exchangeable").checked;
    return rows.filter((r) => {
      if (onlyTraded && !r.traded_today) return false;
      if (onlyStartable && r.conv_not_started) return false;
      if (noExchangeable && r.is_exchangeable) return false;
      return true;
    });
  }

  function sortRows(rows) {
    const { sortKey, sortAsc } = state;
    const sorted = rows.slice().sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // 缺值排最後
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv);
      return av - bv;
    });
    return sortAsc ? sorted : sorted.reverse();
  }

  function render() {
    const rows = sortRows(applyFilters(state.rows));
    document.getElementById("row-count").textContent = `顯示 ${rows.length} / ${state.rows.length} 檔`;

    const tbody = document.getElementById("cb-tbody");
    tbody.replaceChildren();
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.dataset.cbId = r.cb_id;

      const tdId = document.createElement("td"); tdId.textContent = r.cb_id; tr.appendChild(tdId);
      const tdName = document.createElement("td"); tdName.textContent = r.cb_name; tr.appendChild(tdName);

      const tdSignal = document.createElement("td");
      if (r.signal === "candidate" || r.signal === "suspect") {
        const badge = document.createElement("span");
        badge.className = `badge badge-${r.signal}`;
        badge.textContent = SIGNAL_LABEL[r.signal];
        tdSignal.appendChild(badge);
      }
      tr.appendChild(tdSignal);

      const cells = [
        fmtPct(r.premium),
        fmtNum(r.cb_close, 2),
        fmtNum(r.stock_close, 2),
        fmtNum(r.conv_price_now, 2),
        r.volume == null ? "—" : Math.round(r.volume).toLocaleString(),
        r.inst_net_5d == null ? "—" : Math.round(r.inst_net_5d).toLocaleString(),
      ];
      cells.forEach((text, i) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (i === 0) {
          if (r.premium != null && r.premium < 0) td.classList.add("premium-negative");
          if (r.premium == null && r.premium_null_reason) {
            td.title = NULL_REASON_TEXT[r.premium_null_reason] || r.premium_null_reason;
          }
        }
        tr.appendChild(td);
      });
      tr.addEventListener("click", () => openDetail(r));
      tbody.appendChild(tr);
    }

    document.querySelectorAll("th[data-key]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === state.sortKey);
    });
  }

  function openDetail(row) {
    document.getElementById("detail-title").textContent = `${row.cb_id} ${row.cb_name}`;
    const series = state.history
      .filter((h) => h.cb_id === row.cb_id)
      .sort((a, b) => a.date.localeCompare(b.date));

    const chart = document.getElementById("detail-chart");
    const empty = document.getElementById("detail-empty");
    chart.replaceChildren();

    const points = series.filter((s) => s.premium != null);
    if (points.length < 2) {
      empty.hidden = false;
      chart.style.display = "none";
    } else {
      empty.hidden = true;
      chart.style.display = "";
      drawLineChart(chart, points);
    }
    document.getElementById("detail").hidden = false;
  }

  function drawLineChart(svg, points) {
    const W = 600, H = 240, PAD = 32;
    const ys = points.map((p) => p.premium);
    const minY = Math.min(...ys, 0);
    const maxY = Math.max(...ys, 0);
    const spanY = maxY - minY || 1;
    const x = (i) => PAD + (i / (points.length - 1)) * (W - 2 * PAD);
    const y = (v) => H - PAD - ((v - minY) / spanY) * (H - 2 * PAD);

    const zeroY = y(0);
    const zeroLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
    zeroLine.setAttribute("x1", PAD); zeroLine.setAttribute("x2", W - PAD);
    zeroLine.setAttribute("y1", zeroY); zeroLine.setAttribute("y2", zeroY);
    zeroLine.setAttribute("stroke", "#999"); zeroLine.setAttribute("stroke-dasharray", "4 4");
    svg.appendChild(zeroLine);

    const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    path.setAttribute("points", points.map((p, i) => `${x(i)},${y(p.premium)}`).join(" "));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    svg.appendChild(path);

    points.forEach((p, i) => {
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("cx", x(i)); c.setAttribute("cy", y(p.premium)); c.setAttribute("r", 3);
      c.setAttribute("fill", "currentColor");
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${p.date}：${fmtPct(p.premium)}`;
      c.appendChild(title);
      svg.appendChild(c);
    });

    [points[0].date, points[points.length - 1].date].forEach((label, i) => {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", i === 0 ? PAD : W - PAD);
      t.setAttribute("y", H - 8);
      t.setAttribute("text-anchor", i === 0 ? "start" : "end");
      t.setAttribute("font-size", "11");
      t.setAttribute("fill", "#777");
      t.textContent = label;
      svg.appendChild(t);
    });
  }

  function wireUp() {
    document.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (state.sortKey === key) state.sortAsc = !state.sortAsc;
        else { state.sortKey = key; state.sortAsc = true; }
        render();
      });
    });
    ["f-traded", "f-startable", "f-exchangeable"].forEach((id) =>
      document.getElementById(id).addEventListener("change", render));
    document.getElementById("detail-close").addEventListener("click", () => {
      document.getElementById("detail").hidden = true;
    });
    document.getElementById("detail").addEventListener("click", (e) => {
      if (e.target.id === "detail") document.getElementById("detail").hidden = true;
    });
  }

  async function init() {
    try {
      const [latest, history] = await Promise.all([
        fetch("data/latest.json").then((r) => r.json()),
        fetch("data/history.json").then((r) => r.json()),
      ]);
      state.rows = latest.rows;
      state.history = history;

      const m = latest.meta;
      document.getElementById("meta-line").textContent =
        `資料日期：${m.date} ・ ${m.n} 檔 ・ 有股價 ${(m.stock_coverage * 100).toFixed(1)}%` +
        ` ・ 有成交 ${(m.traded_ratio * 100).toFixed(1)}%`;
      const warnList = document.getElementById("warnings");
      m.warnings.forEach((w) => {
        const li = document.createElement("li");
        li.textContent = `⚠ ${w}`;
        warnList.appendChild(li);
      });

      wireUp();
      render();
    } catch (err) {
      document.getElementById("meta-line").textContent = "資料載入失敗，請稍後再試。";
      console.error(err);
    }
  }

  init();
})();
