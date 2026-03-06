/**
 * MakoletDashboard – Shared chart utilities
 *
 * Reusable helpers used across all dashboard pages.
 * Page-specific chart logic lives in the page's own <script> block.
 */

'use strict';

// Design system palette — Option C (mirrors CSS variables)
const PALETTE = {
    profit:  '#22c55e',
    loss:    '#ef4444',
    accent:  '#6366f1',
    surface: '#1e293b',
    border:  '#334155',      // dark card border
    gridLine:'#1e293b',      // subtle grid on dark bg
    tickText:'#94a3b8',      // --text-secondary
};

/**
 * Format a number as Israeli currency.
 * @param {number} amount
 * @returns {string}  e.g. "₪ 12,377.92"
 */
function formatMoney(amount) {
    return '₪\u202F' + Number(amount).toLocaleString('he-IL', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

/**
 * Return a hex color with the given opacity as an rgba string.
 * @param {string} hex   e.g. "#22c55e"
 * @param {number} alpha 0–1
 * @returns {string}
 */
function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Build a Chart.js bar chart for monthly profit comparison.
 *
 * @param {string}   canvasId  ID of the <canvas> element
 * @param {string[]} labels    Month labels (e.g. ["1/2026", "2/2026", ...])
 * @param {number[]} values    Profit values (positive = green, negative = red)
 * @returns {Chart}
 */
function buildProfitBarChart(canvasId, labels, values) {
    const ctx = document.getElementById(canvasId).getContext('2d');

    const colors = values.map(v =>
        v >= 0 ? hexToRgba(PALETTE.profit, 0.85) : hexToRgba(PALETTE.loss, 0.85)
    );
    const borderColors = values.map(v =>
        v >= 0 ? PALETTE.profit : PALETTE.loss
    );

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'רווח משוער (₪)',
                data: values,
                backgroundColor: colors,
                borderColor: borderColors,
                borderWidth: 2,
                borderRadius: 6,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    rtl: true,
                    callbacks: {
                        label: ctx => ' ' + formatMoney(ctx.parsed.y),
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: PALETTE.tickText, font: { size: 12 } },
                    border: { color: PALETTE.border },
                },
                y: {
                    grid: { color: PALETTE.gridLine },
                    border: { color: PALETTE.border },
                    ticks: {
                        color: PALETTE.tickText,
                        font: { size: 11 },
                        callback: v => '₪ ' + Number(v).toLocaleString('he-IL'),
                    },
                },
            },
        },
    });
}
