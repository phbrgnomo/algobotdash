/* Local SVG charts. Financial aggregation belongs exclusively to the API. */
"use strict";
globalThis.ChartView = (() => {
  const totalColor = "#f4f9ff";
  const number = new Intl.NumberFormat("pt-BR", {maximumFractionDigits: 2, minimumFractionDigits: 2});
  const compact = new Intl.NumberFormat("pt-BR", {notation: "compact", maximumFractionDigits: 1});
  const percentage = new Intl.NumberFormat("pt-BR", {style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2});
  const compactPercentage = new Intl.NumberFormat("pt-BR", {style: "percent", notation: "compact", maximumFractionDigits: 1});
  let lastPayload = null;

  function text(tag, value, parent) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (parent) parent.append(node);
    return node;
  }

  function svgNode(tag, attributes, parent) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    if (parent) parent.append(node);
    return node;
  }

  function seriesColor(id) {
    let hash = 2166136261;
    for (const character of id) hash = Math.imul(hash ^ character.codePointAt(0), 16777619);
    return `hsl(${((hash >>> 0) / 4294967296 * 360).toFixed(3)} 65% 65%)`;
  }

  function clear(message) {
    lastPayload = null;
    ["performance", "monetary", "percentage"].forEach((name) => {
      document.querySelector(`#${name}-chart`).replaceChildren(text("p", message));
    });
  }

  function plot(root, {values, xValues, bars, lines, label, inspect, percent = false}) {
    const width = Math.max(290, root.getBoundingClientRect().width || 600);
    const height = 260;
    const left = 62, right = width - 12, top = 18, bottom = height - 35;
    let low = 0, high = 0;
    values.forEach((value) => { low = Math.min(low, value); high = Math.max(high, value); });
    if (low === high) { low -= 1; high += 1; }
    // Normalize before subtraction to keep finite coordinates for very large values.
    const magnitude = Math.max(Math.abs(low), Math.abs(high));
    const scaleLow = low / magnitude, scaleHigh = high / magnitude;
    const y = (value) => bottom - (value / magnitude - scaleLow) / (scaleHigh - scaleLow) * (bottom - top);
    const xFirst = xValues[0], xLast = xValues[xValues.length - 1];
    const span = xLast - xFirst || 1;
    const x = (index) => bars ? left + (index + .5) / bars.length * (right - left)
      : xValues.length === 1 ? (left + right) / 2
        : left + (xValues[index] - xFirst) / span * (right - left);
    const svg = svgNode("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label}, root);
    svgNode("title", {}, svg).textContent = label;
    for (let tick = 0; tick <= 4; tick++) {
      const value = (scaleLow + (scaleHigh - scaleLow) * tick / 4) * magnitude;
      const at = bottom - tick / 4 * (bottom - top);
      svgNode("line", {x1: left, x2: right, y1: at, y2: at, stroke: "#34414a"}, svg);
      svgNode("text", {x: left - 6, y: at + 4, fill: "#9eabb4", "font-size": 11, "text-anchor": "end"}, svg)
        .textContent = percent ? compactPercentage.format(value) : compact.format(value);
    }
    if (bars) {
      const barWidth = Math.min(35, (right - left) / Math.max(bars.length, 1) * .6);
      bars.forEach((value, index) => svgNode("rect", {
        x: Math.max(left, x(index) - barWidth / 2), y: Math.min(y(0), y(value)),
        width: Math.min(barWidth, right - Math.max(left, x(index) - barWidth / 2)),
        height: Math.max(.6, Math.abs(y(value) - y(0))),
        fill: value < 0 ? "#fb7185" : "#59d69b", opacity: .65,
      }, svg));
    }
    lines.forEach((line) => {
      const points = (bars ? `${left},${y(0)} ` : "")
        + line.values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
      svgNode("polyline", {points, fill: "none", stroke: line.color,
        "stroke-width": line.total ? 3.5 : 1.6, opacity: line.total ? 1 : .6,
        "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round"}, svg);
      if (line.values.length === 1) svgNode("circle", {
        cx: x(0), cy: y(line.values[0]), r: line.total ? 4 : 2, fill: line.color,
      }, svg);
    });
    [0, xValues.length - 1].filter((value, index, array) => array.indexOf(value) === index).forEach((index) => {
      svgNode("text", {x: x(index), y: height - 10, fill: "#9eabb4", "font-size": 11,
        "text-anchor": index === 0 ? "start" : "end"}, svg).textContent = labelAt(index);
    });
    function labelAt(index) { return inspect(index, true); }
    const cursor = svgNode("line", {x1: left, x2: left, y1: top, y2: bottom,
      stroke: "#bdc9d2", "stroke-dasharray": "3 3", opacity: 0}, svg);
    const panel = text("div", "", root);
    panel.className = "chart-inspection";
    panel.setAttribute("aria-live", "polite");
    let active = xValues.length - 1;
    function show(index) {
      active = index;
      cursor.setAttribute("x1", x(index)); cursor.setAttribute("x2", x(index));
      cursor.setAttribute("opacity", 1);
      panel.replaceChildren(...inspect(index, false));
    }
    // One interaction surface avoids creating a DOM node for every risk event.
    const hit = svgNode("rect", {x: left, y: top, width: right - left, height: bottom - top,
      fill: "transparent", tabindex: 0, role: "group",
      "aria-label": "Consulte os valores com as setas esquerda e direita, mouse ou toque"}, svg);
    function pointer(event) {
      const rectangle = svg.getBoundingClientRect();
      const pointerX = (event.clientX - rectangle.left) * width / rectangle.width;
      if (bars) {
        show(Math.max(0, Math.min(bars.length - 1, Math.floor((pointerX - left) / (right - left) * bars.length))));
        return;
      }
      const target = xFirst + (pointerX - left) / (right - left) * span;
      let lo = 0, hi = xValues.length - 1;
      while (lo < hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (xValues[mid] < target) lo = mid + 1; else hi = mid;
      }
      if (lo > 0 && Math.abs(xValues[lo - 1] - target) < Math.abs(xValues[lo] - target)) lo--;
      show(lo);
    }
    hit.addEventListener("pointermove", pointer);
    hit.addEventListener("pointerdown", pointer);
    hit.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? 0 : event.key === "End" ? xValues.length - 1
        : active + (event.key === "ArrowRight" ? 1 : -1);
      show(Math.max(0, Math.min(xValues.length - 1, target)));
    });
    show(active);
  }

  function periodLabel(period) {
    const format = (value) => value.split("-").reverse().join("/");
    return `${format(period.start)}${period.start === period.end ? "" : ` a ${format(period.end)}`}${period.partial ? " · parcial" : ""}`;
  }

  function performance(data) {
    const root = document.querySelector("#performance-chart");
    root.replaceChildren();
    const strategies = data.strategies;
    const lines = strategies.map((series) => ({
      color: seriesColor(series.id), values: series.points.map((point) => point.cumulative),
    }));
    lines.push({color: totalColor, total: true, values: data.periods.map((period) => period.cumulative)});
    const legend = text("div", "", root);
    legend.className = "chart-legend";
    [["Total selecionado", totalColor], ...strategies.map((series) => [series.label, seriesColor(series.id)])].forEach(([name, color]) => {
      text("span", name, legend).style.borderColor = color;
    });
    const bars = data.periods.map((period) => period.pnl);
    plot(root, {
      values: bars.concat(lines.flatMap((line) => line.values)),
      xValues: data.periods.map((_, index) => index), bars, lines,
      label: "Resultado realizado por período e P&L acumulado da amostra selecionada",
      inspect(index, labelOnly) {
        const period = data.periods[index];
        if (labelOnly) return period.start.split("-").reverse().join("/");
        const title = text("strong", `${periodLabel(period)} · Total selecionado`);
        const total = text("strong", `Resultado: ${number.format(period.pnl)} · Acumulado: ${number.format(period.cumulative)}`);
        const rows = strategies.map((series) => {
          const point = series.points[index];
          const row = text("div", `${series.label}: ${number.format(point.pnl)} · Acumulado: ${number.format(point.cumulative)}`);
          row.style.borderLeft = `3px solid ${seriesColor(series.id)}`;
          row.style.paddingLeft = "6px";
          return row;
        });
        return [title, total, ...rows];
      },
    });
  }

  function risk(data, name) {
    const root = document.querySelector(`#${name}-chart`);
    root.replaceChildren();
    const curve = data.drawdown[name];
    if (!curve.points.length) {
      const reasons = {
        invalid_opening_balance_coverage: "Saldo de abertura ajustado ausente ou não positivo.",
        numeric_overflow: "Resultado não representável numericamente.",
        realized_metrics_unavailable_for_open_status: "Somente posições realizadas entram neste gráfico.",
      };
      text("p", reasons[curve.reason] || "Sem resultados realizados no período.", root);
      return;
    }
    const percent = name === "percentage";
    const values = curve.points.map((point) => point.depth);
    const format = percent ? (value) => percentage.format(value) : (value) => number.format(value);
    plot(root, {
      values, xValues: curve.points.map((point) => Date.parse(point.at)), percent,
      lines: [{color: "#fb7185", total: true, values}], label: `Drawdown ${percent ? "percentual diário" : "monetário por encerramento"}`,
      inspect(index, labelOnly) {
        const point = curve.points[index];
        const at = new Date(point.at).toLocaleString("pt-BR", {timeZone: data.timezone,
          ...(labelOnly ? {day: "2-digit", month: "2-digit"} : {})});
        return labelOnly ? at : [text("strong", `${at} · ${data.timezone}`), text("strong", `Drawdown: ${format(point.depth)}`)];
      },
    });
  }

  function render(data) {
    if (data.state !== "available" || !data.periods.length) {
      clear(data.state === "empty_selection" ? "Nenhuma combinação selecionada."
        : data.state === "unavailable" ? (data.reason === "numeric_overflow"
          ? "Resultado não representável numericamente." : "Somente posições realizadas entram nos gráficos.")
          : "Sem resultados realizados no período.");
      return;
    }
    lastPayload = data;
    performance(data);
    risk(data, "monetary");
    risk(data, "percentage");
  }
  let resizeTimer;
  globalThis.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (lastPayload) render(lastPayload); }, 100);
  });
  return {clear, render};
})();
