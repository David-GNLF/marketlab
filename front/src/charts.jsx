// Graphiques (Recharts) conformes au guide dataviz : lignes 2px, une seule
// ordonnée, légende dès 2 séries, grille discrète, infobulle au survol,
// couleurs par entité (jamais recyclées).

import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from "recharts";
import { useEffect, useState } from "react";

function useTokens() {
  const lire = () => {
    const s = getComputedStyle(document.documentElement);
    return {
      s1: s.getPropertyValue("--series-1").trim(),
      s2: s.getPropertyValue("--series-2").trim(),
      s3: s.getPropertyValue("--series-3").trim(),
      grid: s.getPropertyValue("--grid").trim(),
      muted: s.getPropertyValue("--muted").trim(),
      surface: s.getPropertyValue("--surface").trim(),
      border: s.getPropertyValue("--border").trim(),
      texte: s.getPropertyValue("--text-primary").trim(),
    };
  };
  const [tokens, setTokens] = useState(lire);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const maj = () => setTokens(lire());
    mq.addEventListener("change", maj);
    return () => mq.removeEventListener("change", maj);
  }, []);
  return tokens;
}

function infobulle(t) {
  return {
    contentStyle: {
      background: t.surface, border: `1px solid ${t.border}`,
      borderRadius: 6, color: t.texte, fontSize: 12,
    },
    labelStyle: { color: t.muted },
  };
}

export function GraphiquePrix({ donnees }) {
  const t = useTokens();
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString("fr-FR"));
  return (
    <ResponsiveContainer width="100%" height={340}>
      <LineChart data={donnees} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={t.grid} vertical={false} />
        <XAxis dataKey="date" tick={{ fill: t.muted, fontSize: 11 }}
               tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={48} />
        <YAxis tick={{ fill: t.muted, fontSize: 11 }} tickLine={false}
               axisLine={false} domain={["auto", "auto"]} width={70}
               tickFormatter={fmt} />
        <Tooltip {...infobulle(t)} formatter={(v) => fmt(v)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="close" name="Cours" stroke={t.s1}
              strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="sma50" name="SMA 50" stroke={t.s2}
              strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="sma200" name="SMA 200" stroke={t.s3}
              strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function GraphiqueEquite({ donnees }) {
  const t = useTokens();
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={donnees} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={t.grid} vertical={false} />
        <XAxis dataKey="date" tick={{ fill: t.muted, fontSize: 11 }}
               tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={48} />
        <YAxis tick={{ fill: t.muted, fontSize: 11 }} tickLine={false}
               axisLine={false} domain={["auto", "auto"]} width={60} />
        <Tooltip {...infobulle(t)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="strategie" name="Stratégie ML" stroke={t.s1}
              strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="buyhold" name="Buy & Hold" stroke={t.s2}
              strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
