"""Dashboard web MarketLab (Streamlit).

Lancer :  streamlit run app/dashboard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from marketlab import (alerts, backtest, config, correlations, eco_calendar,
                       events, forecast, fundamentals, indicators, levels, macro,
                       metalabel, ml, news, notify, paper, score_history,
                       screener, seasonality, signals)
from marketlab.data import get_ohlcv

st.set_page_config(page_title="MarketLab", page_icon="📈", layout="wide")
st.title("📈 MarketLab")
st.caption(
    "Outils d'aide à la décision — analyses statistiques, pas des prédictions. "
    "Aucun contenu ne constitue un conseil en investissement."
)

(tab_analyse, tab_prevision, tab_fonda, tab_correl, tab_resultats, tab_saison,
 tab_screener, tab_macro, tab_calendrier, tab_backtest, tab_ml,
 tab_paper) = st.tabs(
    ["Analyse d'un titre", "🔮 Prévision", "📊 Fondamentaux", "🔗 Corrélations",
     "📣 Résultats", "🗓️ Saisonnalité", "Screener", "Macro", "Calendrier éco",
     "Backtest", "ML", "Paper trading"]
)

# ---------------------------------------------------------------- Analyse
with tab_analyse:
    col1, col2 = st.columns([1, 3])
    with col1:
        univers = st.selectbox("Univers", list(config.UNIVERS), key="a_univers")
        symbole = st.selectbox("Titre", config.UNIVERS[univers], key="a_symbole")
        libre = st.text_input("… ou symbole libre (Yahoo/Binance)")
        if libre.strip():
            symbole = libre.strip().upper()
    try:
        df = indicators.enrich(get_ohlcv(symbole))
        sig = signals.compute_signals(df)
        with col2:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Cours", sig["close"])
            m2.metric("Score composite", sig["score"], signals.label(sig["score"]))
            m3.metric("RSI 14", sig["rsi14"])
            m4.metric("Perf 20 j", f"{sig['ret_20d']} %")
            st.json(sig["signaux"], expanded=False)

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"],
                                     low=df["low"], close=df["close"], name=symbole),
                      row=1, col=1)
        for col, dash in (("sma50", "solid"), ("sma200", "dot")):
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col.upper(),
                                     line={"width": 1, "dash": dash}), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"], name="BB haut",
                                 line={"width": 0.5, "color": "gray"}), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_lower"], name="BB bas",
                                 line={"width": 0.5, "color": "gray"},
                                 fill="tonexty", fillcolor="rgba(128,128,128,0.08)"),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["rsi14"], name="RSI 14"), row=2, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)
        fig.add_trace(go.Bar(x=df.index, y=df["hist"], name="MACD hist"), row=3, col=1)
        fig.update_layout(height=700, xaxis_rangeslider_visible=False,
                          legend_orientation="h", margin={"t": 30})
        st.plotly_chart(fig, use_container_width=True)
        with st.expander("📰 Actualités & sentiment"):
            try:
                senti = news.sentiment(symbole)
                st.write(f"**Sentiment : {senti['lecture']}** "
                         f"(score {senti['score_moyen']}, {senti['n_titres']} titres, "
                         f"{senti.get('positifs', 0)}➕ / {senti.get('negatifs', 0)}➖) "
                         f"— lexical, indicatif seulement")
                st.dataframe(news.headlines(symbole)[["titre", "source", "sentiment"]],
                             use_container_width=True, hide_index=True, height=280)
            except Exception as exc:
                st.warning(f"Actualités indisponibles : {exc}")
    except Exception as exc:
        st.error(f"Données indisponibles pour {symbole} : {exc}")

# ---------------------------------------------------------------- Prévision
with tab_prevision:
    st.caption("Projection probabiliste : un CÔNE de prix, pas un prix cible. "
               "La direction reste peu prévisible — ce sont les intervalles, "
               "la volatilité et le risque qui sont exploitables.")
    cp1, cp2, cp3 = st.columns([2, 1, 1])
    with cp1:
        univers_p = st.selectbox("Univers", list(config.UNIVERS), key="prev_univ")
        symbole_p = st.selectbox("Titre", config.UNIVERS[univers_p], key="prev_sym")
    with cp2:
        horizon_p = st.slider("Horizon (séances)", 5, 60, 20, key="prev_h")
    with cp3:
        sens_p = st.radio("Sens envisagé", ["achat", "vente"], key="prev_sens")

    if st.button("Analyser", type="primary", key="prev_go"):
        try:
            with st.spinner("Simulations en cours…"):
                df_p = indicators.enrich(get_ohlcv(symbole_p, lookback_days=1825))
                reg = forecast.regime(df_p)
                proj = forecast.projeter(df_p, horizon=horizon_p)
                vol_j = forecast.volatilite_ewma(df_p["close"].pct_change().dropna())

            st.subheader(f"Régime : {reg['tendance']} · volatilité {reg['volatilite']}")
            st.write(reg["lecture"])

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Cours", proj["prix_actuel"])
            m2.metric("Médiane projetée", proj["prix_median"],
                      f"{proj['rendement_median_%']:+.2f} %")
            m3.metric("P(hausse)", f"{proj['proba_hausse_%']} %")
            m4.metric("VaR 95 %", f"{proj['var_95_%']} %")
            m5.metric("Vol. annualisée", f"{vol_j * (252 ** 0.5) * 100:.1f} %")

            # cône de projection
            hist = df_p["close"].tail(120)
            futur_idx = list(range(len(hist), len(hist) + horizon_p))
            axe_hist = list(range(len(hist)))
            q = proj["quantiles"]
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(x=axe_hist, y=hist.values, name="Historique",
                                       line=dict(width=2)))
            fig_c.add_trace(go.Scatter(x=futur_idx, y=q["q90"], name="90 %",
                                       line=dict(width=0), showlegend=False))
            fig_c.add_trace(go.Scatter(x=futur_idx, y=q["q10"], name="Intervalle 80 %",
                                       fill="tonexty", line=dict(width=0),
                                       fillcolor="rgba(42,120,214,0.18)"))
            fig_c.add_trace(go.Scatter(x=futur_idx, y=q["q75"], line=dict(width=0),
                                       showlegend=False))
            fig_c.add_trace(go.Scatter(x=futur_idx, y=q["q25"], name="Intervalle 50 %",
                                       fill="tonexty", line=dict(width=0),
                                       fillcolor="rgba(42,120,214,0.35)"))
            fig_c.add_trace(go.Scatter(x=futur_idx, y=q["q50"], name="Médiane",
                                       line=dict(width=2, dash="dash")))
            fig_c.update_layout(height=420, legend_orientation="h",
                                title=f"{symbole_p} — cône de projection à {horizon_p} séances",
                                xaxis_title="séances (120 passées + projection)")
            st.plotly_chart(fig_c, use_container_width=True)

            col_g, col_d = st.columns(2)
            with col_g:
                st.markdown("**Analogues historiques**")
                try:
                    ana = forecast.analogues(df_p, horizon=horizon_p)
                    st.write(f"Sur les {ana['k']} configurations passées les plus "
                             f"proches d'aujourd'hui : hausse dans "
                             f"**{ana['proba_hausse_%']} %** des cas, rendement médian "
                             f"**{ana['rendement_median_%']:+.2f} %** "
                             f"(quartiles {ana['quartile_bas_%']:+.2f} % / "
                             f"{ana['quartile_haut_%']:+.2f} %, extrêmes "
                             f"{ana['pire_%']:+.2f} % / {ana['meilleur_%']:+.2f} %).")
                    st.caption("Dates les plus ressemblantes : "
                               + ", ".join(ana["dates_proches"][:6]))
                except Exception as exc:
                    st.info(f"Analogues indisponibles : {exc}")
            with col_d:
                st.markdown("**Fiabilité des intervalles (calibration)**")
                try:
                    with st.spinner("Contrôle rétroactif…"):
                        cal = forecast.calibration(df_p, horizon=horizon_p,
                                                   n_tests=80, n_sim=3000)
                    st.write(f"Sur {cal['n_tests']} tests passés : l'intervalle "
                             f"annoncé à 80 % a couvert le prix réel "
                             f"**{cal['couverture_80_%']} %** du temps "
                             f"(celui à 50 % : {cal['couverture_50_%']} %). "
                             f"Direction correcte : {cal['direction_correcte_%']} %.")
                    st.caption(cal["verdict"])
                except Exception as exc:
                    st.info(f"Calibration indisponible : {exc}")

            st.divider()
            st.markdown(f"**Plan de position ({sens_p})**")
            try:
                pl = levels.plan(symbole_p, sens=sens_p, horizon=horizon_p,
                                 capital=10_000, risque_pct=1.0)
                p1, p2, p3, p4 = st.columns(4)
                p1.metric("Entrée", pl["entree"])
                p2.metric("Stop", pl["stop"], f"-{pl['risque_%']} %")
                p3.metric("Objectif", pl["objectif"], f"+{pl['gain_potentiel_%']} %")
                p4.metric("Ratio gain/risque", pl["ratio_gain_risque"])
                st.write(f"Probabilité de toucher le stop : "
                         f"**{pl['proba_toucher_stop_%']} %** · l'objectif : "
                         f"**{pl['proba_toucher_objectif_%']} %** · espérance "
                         f"**{pl['esperance_%']:+.2f} %**")
                t = pl["taille"]
                st.write(f"Pour 10 000 $ et 1 % de risque : **{t['quantite']:.4f} "
                         f"unités** ({t['montant']} $), perte plafonnée à "
                         f"{t['perte_si_stop']} $ si le stop est touché.")
                if pl["esperance_par_unite"] <= 0:
                    st.error(pl["lecture"])
                elif pl["ratio_gain_risque"] < 1.5:
                    st.warning(pl["lecture"])
                else:
                    st.success(pl["lecture"])
                zc1, zc2 = st.columns(2)
                zc1.write("Supports : " + (", ".join(
                    f"{z['niveau']} ({z['touches']} touches)"
                    for z in pl["zones"]["supports"]) or "aucun détecté"))
                zc2.write("Résistances : " + (", ".join(
                    f"{z['niveau']} ({z['touches']} touches)"
                    for z in pl["zones"]["resistances"]) or "aucune détectée"))
            except Exception as exc:
                st.error(f"Plan indisponible : {exc}")
        except Exception as exc:
            st.error(f"Analyse impossible : {exc}")

# ---------------------------------------------------------------- Fondamentaux
with tab_fonda:
    st.caption("Valorisation, qualité, croissance et solidité financière. "
               "Actions uniquement — une crypto ou une devise n'a pas de bilan. "
               "Les seuils ne sont pas normalisés par secteur : comparer des "
               "titres comparables.")
    cf1, cf2 = st.columns([1, 2])
    with cf1:
        univers_f = st.selectbox("Univers", ["Actions US", "Actions EU"],
                                 key="fonda_univ")
        symbole_f = st.selectbox("Titre à détailler", config.UNIVERS[univers_f],
                                 key="fonda_sym")
        if st.button("Analyser ce titre", key="fonda_go"):
            st.session_state["fonda_note"] = symbole_f
    with cf2:
        if st.button("Comparer tout l'univers", type="primary", key="fonda_cmp"):
            with st.spinner("Récupération des fondamentaux…"):
                st.session_state["fonda_table"] = fundamentals.comparer(
                    config.UNIVERS[univers_f])

    if st.session_state.get("fonda_note"):
        try:
            n = fundamentals.noter(st.session_state["fonda_note"])
            st.subheader(f"{n['nom'] or n['symbole']} — {n['secteur'] or '—'}")
            g1, g2, g3, g4, g5 = st.columns(5)
            g1.metric("Score global", n["score_global"] if n["score_global"] else "—")
            g2.metric("Valorisation", n["axes"]["valorisation"] or "—")
            g3.metric("Qualité", n["axes"]["qualite"] or "—")
            g4.metric("Croissance", n["axes"]["croissance"] or "—")
            g5.metric("Solidité", n["axes"]["solidite"] or "—")
            st.write(f"**{n['appreciation']}** · dividende "
                     f"{n['dividende_%'] if n['dividende_%'] is not None else '—'} % "
                     f"· bêta {n['beta'] or '—'} · couverture des données "
                     f"{n['couverture_donnees_%']} %")
            detail = pd.DataFrame([
                {"critère": c, "valeur": d["valeur"], "note /100": d["note"]}
                for c, d in n["detail"].items()])
            st.dataframe(detail, use_container_width=True, hide_index=True)
            if n.get("objectif_analystes"):
                st.caption(f"Objectif moyen des analystes : {n['objectif_analystes']} "
                           f"({n.get('nb_analystes') or '?'} analystes) — donnée "
                           "indicative, les objectifs sont souvent optimistes.")
        except Exception as exc:
            st.error(f"Fondamentaux indisponibles : {exc}")

    if st.session_state.get("fonda_table") is not None:
        st.markdown("**Comparatif de l'univers** (classé par score global)")
        st.dataframe(st.session_state["fonda_table"], use_container_width=True,
                     hide_index=True)

# ---------------------------------------------------------------- Corrélations
with tab_correl:
    st.caption("Deux titres qui montent et descendent ensemble ne diversifient "
               "rien. Les corrélations augmentent en période de stress — "
               "précisément quand la diversification serait utile.")
    univers_c = st.multiselect("Univers à analyser", list(config.UNIVERS),
                               default=["Actions US"], key="corr_univ")
    if st.button("Calculer les corrélations", type="primary", key="corr_go"):
        symboles_c = [s for u in univers_c for s in config.UNIVERS.get(u, [])]
        try:
            with st.spinner("Chargement et alignement des historiques…"):
                m = correlations.matrice(symboles_c)
                extremes = correlations.paires_extremes(symboles_c)
                regimes = correlations.correlation_par_regime(symboles_c)
            fig_m = go.Figure(data=go.Heatmap(
                z=m.values, x=list(m.columns), y=list(m.index),
                zmin=-1, zmax=1, colorscale="RdBu", reversescale=True,
                colorbar=dict(title="corr.")))
            fig_m.update_layout(height=520, title="Matrice de corrélation")
            st.plotly_chart(fig_m, use_container_width=True)

            e1, e2 = st.columns(2)
            with e1:
                st.markdown("**Les plus corrélées** — doublons possibles")
                for x in extremes["plus_correlees"]:
                    st.write(f"• {x['a']} / {x['b']} → **{x['correlation']}**")
            with e2:
                st.markdown("**Les moins corrélées** — vraie diversification")
                for x in extremes["moins_correlees"]:
                    st.write(f"• {x['a']} / {x['b']} → **{x['correlation']}**")

            st.info(f"Corrélation moyenne en marché calme : "
                    f"**{regimes['correlation_moyenne_calme']}** · en marché agité : "
                    f"**{regimes['correlation_moyenne_agite']}**. {regimes['lecture']}")
        except Exception as exc:
            st.error(f"Calcul impossible : {exc}")

    st.divider()
    st.markdown("**Risque de ton portefeuille papier**")
    if st.button("Analyser le portefeuille", key="corr_pf"):
        try:
            with st.spinner("Analyse du risque…"):
                a = correlations.analyser_paper()
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Volatilité du portefeuille", f"{a['vol_portefeuille_%']} %")
            r2.metric("Sans diversification", f"{a['vol_sans_diversification_%']} %")
            r3.metric("Bénéfice diversification",
                      f"{a['benefice_diversification_%']} %")
            r4.metric("Positions équivalentes", a["equivalent_positions"])
            st.dataframe(pd.DataFrame(a["lignes"]), use_container_width=True,
                         hide_index=True)
            st.caption("« sur_representation » > 1 = la ligne pèse plus dans le "
                       "risque que dans le capital.")
            st.info(a["lecture"])

            detenus = list(paper.load()["positions"])
            with st.spinner("Recherche de candidats décorrélés…"):
                sugg = correlations.suggerer_diversification(detenus)
            st.markdown("**Pour diversifier davantage** (les moins corrélés à tes "
                        "positions actuelles)")
            st.dataframe(pd.DataFrame(sugg), use_container_width=True,
                         hide_index=True)
        except Exception as exc:
            st.error(f"Analyse impossible : {exc}")

# ---------------------------------------------------------------- Résultats
with tab_resultats:
    st.caption("Les publications trimestrielles concentrent les mouvements les "
               "plus brutaux. Un écart de publication traverse un stop sans "
               "prévenir : mieux vaut savoir qu'une date approche avant d'entrer.")

    st.markdown("**Calendrier des publications à venir**")
    univers_r = st.multiselect("Univers", ["Actions US", "Actions EU"],
                               default=["Actions US"], key="res_univ")
    jours_r = st.slider("Fenêtre (jours)", 7, 90, 45, key="res_j")
    if st.button("Charger le calendrier", type="primary", key="res_cal"):
        symboles_r = [s for u in univers_r for s in config.UNIVERS.get(u, [])]
        with st.spinner("Récupération des dates…"):
            cal = events.prochaines_publications(symboles_r, jours=jours_r)
        if len(cal):
            st.dataframe(cal, use_container_width=True, hide_index=True)
            imminentes = cal[cal["dans_jours"] <= 7]
            if len(imminentes):
                st.warning(f"⚠️ {len(imminentes)} publication(s) dans les 7 jours : "
                           + ", ".join(f"{r['symbole']} ({r['date']})"
                                       for _, r in imminentes.iterrows()))
        else:
            st.info("Aucune publication connue dans cette fenêtre.")
        st.caption("Dates estimées par Yahoo tant que l'entreprise n'a pas "
                   "confirmé — à vérifier auprès de la société.")

    st.divider()
    st.markdown("**Étude d'événements sur un titre**")
    ce1, ce2 = st.columns([1, 2])
    with ce1:
        univers_e = st.selectbox("Univers", ["Actions US", "Actions EU"],
                                 key="evt_univ")
        symbole_e = st.selectbox("Titre", config.UNIVERS[univers_e], key="evt_sym")
        lancer_e = st.button("Étudier", key="evt_go")
    if lancer_e:
        try:
            with st.spinner("Analyse des publications passées…"):
                e = events.etude(symbole_e)
            r = e["reaction_jour_j"]
            st.write(f"**{e['n_publications']} publications analysées** "
                     f"({e['periode'][0]} → {e['periode'][1]}, "
                     f"référence {e['reference']})")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Amplitude moyenne", f"±{r['amplitude_moyenne_%']} %")
            k2.metric("Médiane", f"±{r['amplitude_mediane_%']} %")
            k3.metric("Hausses", f"{r['part_hausses_%']} %")
            k4.metric("Pire séance", f"{r['pire_%']} %")

            if "rendement_anormal_cumule" in e:
                car = e["rendement_anormal_cumule"]
                fig_e = go.Figure()
                fig_e.add_trace(go.Scatter(x=car["jours"], y=car["moyenne_%"],
                                           name="Rendement anormal cumulé",
                                           line=dict(width=2)))
                fig_e.add_vline(x=0, line_dash="dash", line_color="grey")
                fig_e.update_layout(
                    height=340, legend_orientation="h",
                    title=f"{symbole_e} — trajectoire moyenne autour des publications",
                    xaxis_title="séances relatives à la publication (0 = jour J)",
                    yaxis_title="rendement anormal cumulé (%)")
                st.plotly_chart(fig_e, use_container_width=True)
                st.write(f"Avant publication : **{e['derive']['avant_j0_%']:+.2f} %** · "
                         f"après : **{e['derive']['apres_j0_%']:+.2f} %**")
                st.caption(e["derive"]["lecture"])

            if "surprise_vs_reaction" in e:
                sv = e["surprise_vs_reaction"]
                st.info(f"Lien surprise → réaction : corrélation "
                        f"**{sv['correlation']}** sur {sv['n']} publications. "
                        f"{sv['lecture']}")

            risque = events.risque_evenement(symbole_e, horizon=20)
            if risque.get("dans_horizon"):
                st.error(risque["message"])
            else:
                st.success(risque["message"])
        except Exception as exc:
            st.error(f"Étude impossible : {exc}")

# ---------------------------------------------------------------- Saisonnalité
with tab_saison:
    st.caption("La saisonnalité est le terrain de jeu du sur-apprentissage : "
               "en testant 12 mois, on trouve presque toujours un mois "
               "« significatif » par hasard. Chaque effet est donc soumis à "
               "trois garde-fous — test de Student, correction de Bonferroni "
               "pour la multiplicité, et stabilité sur les deux moitiés de "
               "l'historique. Seuls les effets qui franchissent les trois sont "
               "retenus.")
    cs1, cs2 = st.columns([1, 2])
    with cs1:
        univers_s = st.selectbox("Univers", list(config.UNIVERS), key="sais_univ")
        symbole_s = st.selectbox("Titre", config.UNIVERS[univers_s], key="sais_sym")
        lancer_s = st.button("Analyser la saisonnalité", type="primary",
                             key="sais_go")
    if lancer_s:
        try:
            with st.spinner("Analyse sur l'historique le plus long disponible…"):
                a = seasonality.analyser(symbole_s)
                mois = pd.DataFrame(a["par_mois"])
                courbe = seasonality.courbe_annuelle(symbole_s)

            if a["effets_retenus"]:
                st.success(a["conclusion"])
            else:
                st.info(a["conclusion"])
            st.caption(f"Historique : {a.get('n_annees', '?')} années.")

            # rendement moyen par mois : retenus en couleur, autres en gris
            couleurs = ["#2a78d6" if r else "#898781" for r in mois["retenu"]]
            fig_s = go.Figure(go.Bar(x=mois["mois"], y=mois["rendement_moyen_%"],
                                     marker_color=couleurs,
                                     hovertemplate="%{x} : %{y:.2f} %<extra></extra>"))
            fig_s.update_layout(height=330, title="Rendement mensuel moyen "
                                                 "(en bleu : effets retenus)",
                                yaxis_title="%")
            st.plotly_chart(fig_s, use_container_width=True)
            st.dataframe(mois, use_container_width=True, hide_index=True)

            fig_c = go.Figure(go.Scatter(x=courbe["jours"],
                                         y=courbe["cumul_moyen_%"],
                                         line=dict(width=2)))
            fig_c.update_layout(
                height=300,
                title=f"Trajectoire moyenne au fil de l'année "
                      f"({courbe['n_annees']} années superposées)",
                xaxis_title="jour de l'année", yaxis_title="rendement cumulé (%)")
            st.plotly_chart(fig_c, use_container_width=True)

            sc1, sc2 = st.columns(2)
            with sc1:
                st.markdown("**Jour de la semaine**")
                if isinstance(a.get("par_jour_semaine"), list):
                    st.dataframe(pd.DataFrame(a["par_jour_semaine"]),
                                 use_container_width=True, hide_index=True)
            with sc2:
                st.markdown("**Période du mois**")
                if isinstance(a.get("par_periode_du_mois"), list):
                    st.dataframe(pd.DataFrame(a["par_periode_du_mois"]),
                                 use_container_width=True, hide_index=True)

            h = a.get("halloween", {})
            if "erreur" not in h:
                st.markdown("**« Sell in May » (novembre-avril vs mai-octobre)**")
                h1, h2, h3 = st.columns(3)
                h1.metric("Novembre-avril", f"{h['novembre_avril_%']} %")
                h2.metric("Mai-octobre", f"{h['mai_octobre_%']} %")
                h3.metric("Écart", f"{h['ecart_points']} pts")
                (st.success if h["significatif"] else st.info)(h["lecture"])
        except Exception as exc:
            st.error(f"Analyse impossible : {exc}")

# ---------------------------------------------------------------- Screener
with tab_screener:
    univers_s = st.multiselect("Univers à balayer", list(config.UNIVERS),
                               default=["Actions US", "Crypto"])
    if st.button("Lancer le scan", type="primary"):
        symbols = [s for u in univers_s for s in config.UNIVERS[u]]
        with st.spinner(f"Scan de {len(symbols)} titres…"):
            table = screener.scan(symbols)
        st.dataframe(table, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- Macro
with tab_macro:
    if st.button("Actualiser le tableau macro", type="primary"):
        with st.spinner("Récupération FRED…"):
            snap = macro.snapshot()
            reg = macro.regime()
        st.subheader(f"Régime : {reg['lecture']} (score {reg['score']})")
        for note in reg["notes"]:
            st.write("• " + note)
        st.dataframe(snap, use_container_width=True, hide_index=True)
        st.line_chart(macro.inflation_yoy(), height=250)
        st.caption("Inflation US en glissement annuel (%)")

# ---------------------------------------------------------------- Calendrier
with tab_calendrier:
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        impacts = st.multiselect("Impact", ["High", "Medium", "Low"],
                                 default=["High", "Medium"],
                                 format_func=lambda i: eco_calendar.IMPACT_FR.get(i, i))
    with col_f2:
        devises = st.multiselect("Devises", ["USD", "EUR", "GBP", "JPY", "CHF",
                                             "AUD", "CAD", "NZD", "CNY"],
                                 default=["USD", "EUR"])
    try:
        prochains = eco_calendar.upcoming(hours=24)
        if len(prochains):
            st.warning(f"⚡ {len(prochains)} événement(s) à FORT impact dans les "
                       f"prochaines 24 h (heure Bénin)")
        evts = eco_calendar.get_events(currencies=devises or None,
                                       impacts=impacts or None)
        evts = evts[evts["quand"] >= pd.Timestamp.now().normalize()]  # dès aujourd'hui
        affiche = evts[["quand", "devise", "evenement", "impact_fr",
                        "prevision", "precedent"]].copy()
        affiche["quand"] = affiche["quand"].dt.strftime("%a %d/%m %H:%M")
        st.dataframe(affiche, use_container_width=True, hide_index=True, height=500)
        st.caption("Source : ForexFactory (semaine en cours ; bascule sur la semaine "
                   "suivante chaque dimanche), heure du Bénin (UTC+1).")
    except Exception as exc:
        st.error(f"Calendrier indisponible : {exc}")

    st.divider()
    if alerts.est_configure():
        st.success("Alertes actives ✅ — canaux : "
                   f"{', '.join(notify.canaux_actifs())}. "
                   "La tâche `\\MarketLab\\Alertes` tourne toutes les heures.")
    else:
        st.info("Aucun canal d'alerte configuré. Lancer "
                "`.venv\\Scripts\\python scripts\\configurer_alertes.py` "
                "— ntfy (sans compte), e-mail, notification Windows ou Telegram.")

# ---------------------------------------------------------------- Backtest
with tab_backtest:
    col1, col2 = st.columns([1, 3])
    with col1:
        univers_b = st.selectbox("Univers", list(config.UNIVERS), key="b_univers")
        symbole_b = st.selectbox("Titre", config.UNIVERS[univers_b], key="b_symbole")
        frais = st.slider("Frais par transaction (bps)", 0, 50, 10)
        lancer = st.button("Backtester", type="primary")
    if lancer:
        try:
            df_b = indicators.enrich(get_ohlcv(symbole_b, lookback_days=1825))
            with col2:
                st.subheader(f"Stratégies sur {symbole_b} (5 ans, frais {frais} bps)")
                comp = {name: backtest.run(df_b, strat, fee_bps=frais)
                        for name, strat in backtest.STRATEGIES.items()}
                st.dataframe(pd.DataFrame({n: r["metrics"] for n, r in comp.items()}).T,
                             use_container_width=True)
                fig_eq = go.Figure()
                for name, r in comp.items():
                    fig_eq.add_trace(go.Scatter(x=r["equity"].index, y=r["equity"],
                                                name=name))
                fig_eq.update_layout(height=400, title="Courbes d'équité (base 1)",
                                     legend_orientation="h")
                st.plotly_chart(fig_eq, use_container_width=True)
        except Exception as exc:
            st.error(f"Backtest impossible : {exc}")

# ---------------------------------------------------------------- ML
with tab_ml:
    st.caption("Validation walk-forward stricte (entraînement sur le passé "
               "uniquement, ré-entraîné à chaque bloc). AUC 0.50 = hasard ; "
               "se méfier de tout résultat trop beau.")
    sec_ic, sec_model = st.columns(2)

    with sec_ic:
        st.subheader("Pouvoir prédictif du score composite")
        univers_ic = st.selectbox("Univers", list(config.UNIVERS), key="ic_univers")
        horizon_ic = st.slider("Horizon (bougies)", 5, 30, 10, key="ic_h")
        if st.button("Mesurer (IC de Spearman)"):
            with st.spinner("Calcul rétroactif des scores…"):
                table_ic = score_history.ic_table(config.UNIVERS[univers_ic],
                                                  horizon=horizon_ic)
            st.dataframe(table_ic, use_container_width=True, hide_index=True)
            st.caption("IC > 0.05 stable = signal exploitable ; ~0 = score sans "
                       "valeur prédictive à cet horizon.")

    with sec_model:
        st.subheader("Modèle ML (direction à N jours)")
        univers_ml = st.selectbox("Univers", list(config.UNIVERS), key="ml_univers")
        symbole_ml = st.selectbox("Titre", config.UNIVERS[univers_ml], key="ml_symbole")
        horizon_ml = st.slider("Horizon (bougies)", 3, 20, 5, key="ml_h")
        seuil_ml = st.slider("Seuil P(hausse) pour être investi", 0.50, 0.70, 0.55)
        if st.button("Entraîner (walk-forward)", type="primary"):
            try:
                with st.spinner("Walk-forward en cours…"):
                    df_ml = indicators.enrich(get_ohlcv(symbole_ml, lookback_days=1825))
                    res = ml.walk_forward(df_ml, horizon=horizon_ml, threshold=seuil_ml)
                st.json(res["metrics"], expanded=True)
                fig_ml = go.Figure()
                fig_ml.add_trace(go.Scatter(x=res["equity"].index, y=res["equity"],
                                            name="Stratégie ML"))
                fig_ml.add_trace(go.Scatter(x=res["bh_equity"].index, y=res["bh_equity"],
                                            name="Buy & Hold"))
                fig_ml.update_layout(height=350, title="Équité hors échantillon (base 1)",
                                     legend_orientation="h")
                st.plotly_chart(fig_ml, use_container_width=True)
                st.dataframe(res["folds"], use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Entraînement impossible : {exc}")

    st.divider()
    st.subheader("Méta-labeling — faut-il suivre le signal ?")
    st.caption("Au lieu de prédire la direction (problème quasi insoluble), un "
               "second modèle estime si le signal du moment mérite d'être suivi. "
               "Étiquetage par triple barrière (objectif 2×ATR, stop 1,5×ATR, "
               "limite temporelle). Le juge de paix est la **séquence "
               "exécutable** — un seul trade à la fois — et non l'espérance par "
               "trade, qui surpondère les périodes à signaux denses.")
    cm1, cm2, cm3 = st.columns(3)
    with cm1:
        univers_mt = st.selectbox("Univers", list(config.UNIVERS), key="mt_univ")
        symbole_mt = st.selectbox("Titre", config.UNIVERS[univers_mt], key="mt_sym")
    with cm2:
        horizon_mt = st.slider("Horizon (séances)", 5, 40, 20, key="mt_h")
    with cm3:
        seuil_mt = st.slider("Seuil de confiance du méta", 0.45, 0.75, 0.55,
                             key="mt_s")
    if st.button("Lancer le méta-labeling", key="mt_go"):
        try:
            with st.spinner("Triple barrière et walk-forward…"):
                r = metalabel.analyser(symbole_mt, horizon=horizon_mt,
                                       seuil_meta=seuil_mt)
            n1, n2, n3 = st.columns(3)
            n1.metric("Signaux primaires", r["n_signaux_primaires"])
            n2.metric("Retenus par le méta", r["n_retenus_par_meta"],
                      f"-{r['part_filtree_%']} % filtrés")
            n3.metric("AUC du méta-modèle", r["meta_auc_moyen"])
            st.dataframe(pd.DataFrame([
                {"stratégie": "signal primaire seul", **r["primaire"]},
                {"stratégie": "signal filtré par le méta", **r["avec_meta"]},
            ]), use_container_width=True, hide_index=True)
            texte = r["synthese"]
            if texte.startswith("⚠️") or "dégrade" in texte:
                st.error(texte)
            elif "apporte de la valeur" in texte:
                st.success(texte)
            else:
                st.warning(texte)
        except Exception as exc:
            st.error(f"Méta-labeling impossible : {exc}")

# ---------------------------------------------------------------- Paper
with tab_paper:
    st.caption("Portefeuille virtuel (USD) — exécution au dernier cours connu, "
               "sans spread ni slippage : les performances papier sont optimistes.")
    if not paper.PORTFOLIO_PATH.exists():
        cap = st.number_input("Capital initial (USD)", 1000.0, 1_000_000.0, 10_000.0,
                              step=1000.0)
        if st.button("Créer le portefeuille papier", type="primary"):
            paper.init(cap)
            st.rerun()
    else:
        try:
            e = paper.etat()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Valeur totale", f"{e['valeur_totale_usd']} $",
                      f"{e['perf_totale_%']:+.2f} %")
            c2.metric("Cash", f"{e['cash_usd']} $")
            c3.metric("Positions", f"{e['valeur_positions_usd']} $")
            c4.metric("Transactions", e["nb_transactions"])
            if len(e["positions"]):
                st.dataframe(e["positions"], use_container_width=True, hide_index=True)
            else:
                st.info("Aucune position ouverte.")
        except Exception as exc:
            st.error(f"Valorisation impossible : {exc}")

        st.divider()
        col_a, col_v, col_auto = st.columns(3)
        with col_a:
            st.subheader("Acheter")
            sym_a = st.text_input("Symbole", key="pp_buy_sym")
            mnt_a = st.number_input("Montant (USD)", 50.0, 1_000_000.0, 1000.0,
                                    step=100.0, key="pp_buy_amt")
            if st.button("Acheter (papier)") and sym_a.strip():
                try:
                    t = paper.acheter(sym_a.strip().upper(), mnt_a)
                    st.success(f"ACHAT {t['symbole']} : {t['qty']} @ {t['prix_usd']} $")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with col_v:
            st.subheader("Vendre")
            pf_now = paper.load()
            if pf_now["positions"]:
                sym_v = st.selectbox("Position", list(pf_now["positions"]),
                                     key="pp_sell_sym")
                if st.button("Vendre tout (papier)"):
                    try:
                        t = paper.vendre(sym_v)
                        st.success(f"VENTE {t['symbole']} : PnL {t['pnl_usd']} $")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            else:
                st.write("(aucune position)")
        with col_auto:
            st.subheader("Signaux auto")
            dry = st.checkbox("Simulation seule (dry-run)", value=True)
            if st.button("Exécuter les signaux du screener"):
                with st.spinner("Scan et décisions…"):
                    journal = paper.auto(dry_run=dry)
                for ligne in journal:
                    st.write("• " + ligne)
