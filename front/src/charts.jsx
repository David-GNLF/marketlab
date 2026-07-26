// Graphiques (Recharts) conformes au guide dataviz : lignes de 2 px, une seule
// ordonnée, légende dès deux séries, grille discrète, infobulle au survol,
// couleur attachée à l'entité et jamais recyclée.

import { useEffect, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

function useTokens() {
  const lire = () => {
    const s = getComputedStyle(document.documentElement);
    const v = (n) => s.getPropertyValue(n).trim();
    return {
      s1: v("--series-1"), s2: v("--series-2"), s3: v("--series-3"),
      grid: v("--grid"), muted: v("--muted"), surface: v("--surface"),
      border: v("--border"), texte: v("--text-primary"),
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

const infobulle = (t) => ({
  contentStyle: {
    background: t.surface, border: `1px solid ${t.border}`,
    borderRadius: 6, color: t.texte, fontSize: 12,
  },
  labelStyle: { color: t.muted },
});

const fmt = (v) =>
  v == null ? "—" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 2 });

export function GraphiquePrix({ donnees }) {
  const t = useTokens();
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={donnees} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={t.grid} vertical={false} />
        <XAxis dataKey="date" tick={{ fill: t.muted, fontSize: 11 }}
               tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={56} />
        <YAxis tick={{ fill: t.muted, fontSize: 11 }} tickLine={false}
               axisLine={false} domain={["auto", "auto"]} width={72}
               tickFormatter={fmt} />
        <Tooltip {...infobulle(t)} formatter={fmt} />
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

// Cône de projection : bande d'incertitude à 80 % + médiane, raccordées aux
// dernières séances observées.
export function GraphiqueCone({ projection, historique }) {
  const t = useTokens();
  const q = projection?.quantiles;
  if (!q?.q50) return null;

  const passe = (historique ?? []).slice(-60).map((p) => ({
    etiquette: p.date, cours: p.close,
  }));
  const futur = q.q50.map((median, i) => ({
    etiquette: `J+${i + 1}`,
    bas: q.q10[i],
    // l'aire empilée part du bas : on trace l'épaisseur, pas la valeur haute
    epaisseur: q.q90[i] - q.q10[i],
    median,
  }));

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={[...passe, ...futur]}
                     margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={t.grid} vertical={false} />
        <XAxis dataKey="etiquette" tick={{ fill: t.muted, fontSize: 11 }}
               tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={56} />
        <YAxis tick={{ fill: t.muted, fontSize: 11 }} tickLine={false}
               axisLine={false} domain={["auto", "auto"]} width={72}
               tickFormatter={fmt} />
        <Tooltip {...infobulle(t)} formatter={fmt} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area type="monotone" dataKey="bas" stackId="cone" stroke="none"
              fill="none" legendType="none" isAnimationActive={false} />
        <Area type="monotone" dataKey="epaisseur" stackId="cone" stroke="none"
              fill={t.s1} fillOpacity={0.18} name="Intervalle 80 %"
              isAnimationActive={false} />
        <Line type="monotone" dataKey="cours" name="Cours observé" stroke={t.s1}
              strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="median" name="Médiane projetée"
              stroke={t.s2} strokeWidth={2} strokeDasharray="5 4" dot={false}
              isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
