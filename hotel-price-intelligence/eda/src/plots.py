"""Ve hinh Wave A TU BANG DA AGGREGATE (khong bao gio tu observation-level): histogram tu bin count SQL, box plot tu quantile SQL
(`Axes.bxp`), heatmap tu pivot nho. Moi ham nhan DataFrame (dau ra `wave_a.compute_wave_a_tables`) va tra ve `Figure`; KHONG goi
`plt.show()`/`savefig` - notebook luu qua `wave_a.save_figure()` (kiem ten hinh thuoc `publication.PUBLISHED_FIGURES`).

Hinh tren bang RONG (vd fixture nho) van ve duoc (hien chu "khong co du lieu") de pipeline khong vo vi 1 bang trong.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.ticker import MaxNLocator

import metrics

_STATUS_COLORS = {"success": "#2a9d8f", "sold_out": "#e9c46a", "not_bookable": "#f4a261", "partial": "#8d99ae", "error": "#e76f51"}


def _no_data(ax, message: str = "khong co du lieu") -> None:
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, color="gray")
    ax.set_xticks([])
    ax.set_yticks([])


def _fig(figsize=(9, 5)):
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


def plot_run_item_by_source_crawl_date(df: pd.DataFrame):
    fig, ax = _fig()
    if df.empty:
        _no_data(ax)
    for source, group in df.groupby("source_code"):
        ax.plot(pd.to_datetime(group["vn_crawl_date"]), group["items"], marker="o", markersize=3, label=source)
    if not df.empty:
        ax.legend()
        fig.autofmt_xdate()
    ax.set_ylabel("so item RAW / ngay crawl (VN)")
    ax.set_title("Item RAW theo ngay crawl va nguon")
    fig.tight_layout()
    return fig


def plot_raw_vs_main_by_source(df: pd.DataFrame):
    fig, ax = _fig((8, 4.5))
    if df.empty:
        _no_data(ax)
    else:
        positions = np.arange(len(df))
        width = 0.38
        ax.bar(positions - width / 2, df["n_items_raw"], width, label="RAW")
        ax.bar(positions + width / 2, df["n_items_main"], width, label="MAIN")
        ax.set_xticks(positions)
        ax.set_xticklabels(df["source_code"])
        ax.legend()
    ax.set_ylabel("so item")
    ax.set_title("RAW so voi MAIN theo nguon (item)")
    fig.tight_layout()
    return fig


def plot_owner_outcome_rate_by_source_date(df: pd.DataFrame):
    fig, ax = _fig()
    if df.empty:
        _no_data(ax)
    for source, group in df.groupby("owner_source"):
        ax.plot(pd.to_datetime(group["crawl_date"]), group["owner_success_rate"], marker="o", markersize=3, label=f"{source} owner_success")
        ax.plot(pd.to_datetime(group["crawl_date"]), group["missing_rate"], linestyle="--", label=f"{source} missing")
    if not df.empty:
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
    ax.set_ylim(-0.02, 1.02)
    ax.set_ylabel("ty le tren n_scheduled")
    ax.set_title("Ty le owner success / missing theo ngay crawl va nguon")
    fig.tight_layout()
    return fig


def plot_run_duration_by_source(df: pd.DataFrame):
    fig, ax = _fig()
    if df.empty:
        _no_data(ax)
    for source, group in df.groupby("source_code"):
        ax.hist(group["duration_minutes"].astype(float), bins=30, alpha=0.6, label=source)
    if not df.empty:
        ax.legend()
    ax.set_xlabel("duration_minutes (chi run production/protocol - notebook truyen bang da loc is_protocol_run)")
    ax.set_title("Phan bo thoi luong run production theo nguon")
    fig.tight_layout()
    return fig


def plot_finish_hour_distribution(df: pd.DataFrame):
    """Hinh chinh CHI ve run PRODUCTION (`is_protocol_run`, file 17 M2): run pilot/pre-protocol (10 run, khong item owner) van nam trong bang RAW nhung khong
    lam nhieu bieu do capacity."""
    fig, ax = _fig()
    production = df[df["is_protocol_run"].astype(bool)] if "is_protocol_run" in df.columns else df
    if production.empty:
        _no_data(ax)
    else:
        pivot = production.pivot_table(index="finish_hour_vn", columns="source_code", values="n_runs", aggfunc="sum", fill_value=0)
        pivot = pivot.reindex(range(24), fill_value=0)
        pivot.plot(kind="bar", ax=ax, width=0.85)
    ax.set_xlabel("gio VN hoan thanh run")
    ax.set_ylabel("so run production")
    ax.set_title("Thoi diem hoan thanh run production (protocol) theo gio Viet Nam")
    fig.tight_layout()
    return fig


def plot_active_hotel_by_date(df: pd.DataFrame, cohort: "pd.DataFrame | None" = None):
    """Active hotel theo ngay crawl va nguon. Truc y tu co de thay thay doi nho nen `cohort` (bang `cohort_attrition_by_version`) duoc dung de chu thich moi
    lan doi cohort (vd 355 -> 354): duong dung dut + nhan `cohort vX: a -> b tu ngay` (file 17 MINOR 2) - khong ep truc y ve 0 (se che tin hieu)."""
    fig, ax = _fig()
    if df.empty:
        _no_data(ax)
    for source, group in df.groupby("source_code"):
        ax.plot(pd.to_datetime(group["vn_crawl_date"]), group["n_active_hotels"], marker="o", markersize=3, label=source)
    if not df.empty:
        ax.legend(loc="lower left")
        fig.autofmt_xdate()
        if cohort is not None and not cohort.empty:
            previous_size = None
            for row in cohort.sort_values("effective_from_crawl_date").itertuples():
                if previous_size is not None and int(row.size) != previous_size:
                    when = pd.to_datetime(row.effective_from_crawl_date)
                    ax.axvline(when, color="gray", linestyle=":", linewidth=1)
                    ax.annotate(f"cohort {row.cohort_version}: {previous_size} -> {int(row.size)}\ntu {when.date()} ({row.change_type})", xy=(when, 0.97),
                                xycoords=("data", "axes fraction"), xytext=(4, 0), textcoords="offset points", ha="left", va="top", fontsize=8, color="dimgray")
                previous_size = int(row.size)
    ax.set_ylabel("so hotel active (truc y tu co, khong bat dau tu 0)")
    ax.set_title("Active hotel theo ngay crawl (VN) va nguon")
    fig.tight_layout()
    return fig


def plot_crawl_date_lead_time_heatmap(df: pd.DataFrame):
    fig, ax = _fig((10, 6))
    if df.empty:
        _no_data(ax)
    else:
        pivot = df.pivot_table(index="vn_crawl_date", columns="lead_time_bucket", values="n_items", aggfunc="sum", fill_value=0)
        columns = [c for c in metrics.LEAD_TIME_BUCKET_ORDER if c in pivot.columns] + [c for c in pivot.columns if c not in metrics.LEAD_TIME_BUCKET_ORDER]
        sns.heatmap(pivot[columns], ax=ax, cmap="viridis", cbar_kws={"label": "so item MAIN"})
    ax.set_title("So item MAIN theo ngay crawl x lead-time bucket")
    ax.set_xlabel("lead-time bucket (ngay)")
    ax.set_ylabel("ngay crawl (VN)")
    fig.tight_layout()
    return fig


def _annotate_anchor_counts(ax, bars, anchors) -> None:
    """Ghi so ngay check-in (anchor) phan biet len dau moi cot: cot cao (nhieu item) co the chi la MOT ngay lap qua nhieu hotel/crawl day (file 17 M4)."""
    top = max((bar.get_height() for bar in bars), default=0)
    for bar, anchor in zip(bars, anchors):
        ax.annotate(f"{int(anchor)} ngay", (bar.get_x() + bar.get_width() / 2, bar.get_height()), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8, color="black")
    ax.set_ylim(0, top * 1.12 if top else 1)


def plot_checkin_coverage_weekday_month(weekday_df: pd.DataFrame, month_df: pd.DataFrame):
    """Owned item theo thu / thang check-in; NHAN tren cot = so ngay check-in (anchor) PHAN BIET cua nhom - day la coverage cua cac anchor da chon, KHONG phai
    bang chung weekday effect (file 17 M4)."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    if weekday_df.empty:
        _no_data(axes[0])
    else:
        colors = ["#e76f51" if weekend else "#457b9d" for weekend in weekday_df["is_weekend_fri_sat"]]
        bars = axes[0].bar(weekday_df["weekday"], weekday_df["n_items"], color=colors)
        axes[0].tick_params(axis="x", rotation=45)
        if "n_distinct_checkin_dates" in weekday_df.columns:
            _annotate_anchor_counts(axes[0], bars, weekday_df["n_distinct_checkin_dates"])
    axes[0].set_title("Owned item theo thu check-in (do = Thu Sau/Bay)\nnhan tren cot = so ngay check-in phan biet (anchor)")
    if month_df.empty:
        _no_data(axes[1])
    else:
        bars = axes[1].bar(month_df["checkin_month"], month_df["n_items"], color="#2a9d8f")
        axes[1].tick_params(axis="x", rotation=45)
        if "n_distinct_checkin_dates" in month_df.columns:
            _annotate_anchor_counts(axes[1], bars, month_df["n_distinct_checkin_dates"])
    axes[1].set_title("Owned item theo thang check-in\nnhan tren cot = so ngay check-in phan biet (anchor)")
    fig.tight_layout()
    return fig


def plot_lead_time_bucket_distribution(df: pd.DataFrame):
    fig, ax = _fig((8, 4.5))
    if df.empty:
        _no_data(ax)
    else:
        ax.bar(df["lead_time_bucket"], df["n_items"], color="#457b9d")
    ax.set_title("Phan bo owned item MAIN theo lead-time bucket")
    ax.set_xlabel("lead-time bucket (ngay)")
    ax.set_ylabel("so item")
    fig.tight_layout()
    return fig


def plot_price_histograms(linear: pd.DataFrame, log10: pd.DataFrame):
    """Ve tu BIN COUNT SQL (khong keo observation vao Python). Truc X cua ve phai la log10(gia), khong phai chi set_yscale('log')."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    if linear.empty:
        _no_data(axes[0])
    else:
        axes[0].bar(linear["bin_lo"], linear["n_obs"], width=(linear["bin_hi"] - linear["bin_lo"]), align="edge", color="#457b9d")
    axes[0].set_title("Histogram gia (thang thuong, 80 bin deu)")
    axes[0].set_xlabel("price_per_night (VND)")
    axes[0].set_ylabel("so observation")
    if log10.empty:
        _no_data(axes[1])
    else:
        axes[1].bar(log10["log10_lo"], log10["n_obs"], width=(log10["log10_hi"] - log10["log10_lo"]), align="edge", color="#2a9d8f")
    axes[1].set_title("Histogram log10(gia) - thay ro duoi phan phoi")
    axes[1].set_xlabel("log10(price_per_night)")
    fig.tight_layout()
    return fig


def plot_price_box_by_city(box: pd.DataFrame):
    """Box plot tu quantile SQL: hop = P25-P75, vach giua = median, rau = P5-P95, cham = mean (khong ve outlier)."""
    fig, ax = _fig()
    if box.empty:
        _no_data(ax)
    else:
        stats = [{"label": row.city, "whislo": row.p5, "q1": row.p25, "med": row.p50, "q3": row.p75, "whishi": row.p95,
                  "mean": row.mean_price, "fliers": []} for row in box.itertuples()]
        ax.bxp(stats, showfliers=False, showmeans=True)
        ax.tick_params(axis="x", rotation=30)
    ax.set_ylabel("price_per_night (VND)")
    ax.set_title("Phan bo gia theo thanh pho (hop P25-P75, rau P5-P95, cham = mean)")
    fig.tight_layout()
    return fig


def plot_price_by_lead_time_bucket(df: pd.DataFrame):
    fig, ax = _fig()
    if df.empty:
        _no_data(ax)
    else:
        frame = df.set_index("lead_time_bucket").reindex([b for b in metrics.LEAD_TIME_BUCKET_ORDER if b in set(df["lead_time_bucket"])])
        x = np.arange(len(frame))
        ax.fill_between(x, frame["p5"], frame["p95"], alpha=0.25, label="P5-P95")
        ax.plot(x, frame["p75"], linestyle="--", label="P75")
        ax.plot(x, frame["p50"], marker="o", label="median")
        ax.set_xticks(x)
        ax.set_xticklabels(frame.index)
        ax.legend()
    ax.set_xlabel("lead-time bucket (ngay)")
    ax.set_ylabel("price_per_night (VND)")
    ax.set_title("Gia theo lead-time bucket (MAIN)")
    fig.tight_layout()
    return fig


def plot_item_availability_stacked(df: pd.DataFrame, *, group_col: str, title: str):
    """Cot xep chong ty le 5 status terminal (item grain) - moi status co mat ke ca = 0 (khong am tham bo)."""
    fig, ax = _fig((9, 5))
    if df.empty:
        _no_data(ax)
    else:
        rates = df.set_index(group_col)[[f"{s}_rate" for s in metrics.TERMINAL_ITEM_STATUSES]]
        rates.columns = list(metrics.TERMINAL_ITEM_STATUSES)
        rates.plot(kind="bar", stacked=True, ax=ax, color=[_STATUS_COLORS[s] for s in rates.columns], width=0.8)
        # Legend NGOAI truc: legend trong khung tung che phan tren cua cot ben phai (phan sold_out/not_bookable/error - chinh la thu can doc).
        ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
        ax.tick_params(axis="x", rotation=30)
    ax.set_ylabel("ty le tren n_items")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def plot_missingness_heatmap(df: pd.DataFrame):
    fig, ax = _fig((8, 7))
    if df.empty:
        _no_data(ax)
    else:
        pivot = df.pivot_table(index=["field_group", "field"], columns="source_code", values="null_rate")
        sns.heatmap(pivot, ax=ax, annot=True, fmt=".3f", cmap="Reds", vmin=0, vmax=max(0.01, float(np.nanmax(pivot.values))),
                    cbar_kws={"label": "null_rate"})
    ax.set_title("Ty le NULL theo field x nguon (observation available, MAIN)")
    fig.tight_layout()
    return fig


def plot_reference_coverage_by_lead_time(exact_main: pd.DataFrame, legacy_series_exists: pd.DataFrame):
    """2 metric KHAC dinh nghia (khong duoc gop): exact approved-key (MAIN, chat) vs series-has-approved-reference (RAW, long, bucket legacy)."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    if exact_main.empty:
        _no_data(axes[0])
    else:
        axes[0].bar(exact_main["lead_time_bucket"], exact_main["match_rate"], color="#457b9d")
        axes[0].set_ylim(0, max(0.05, float(exact_main["match_rate"].max()) * 1.2))
    axes[0].set_title("Exact approved-key coverage (MAIN)")
    axes[0].set_ylabel("ty le observation")
    if legacy_series_exists.empty:
        _no_data(axes[1])
    else:
        axes[1].bar(legacy_series_exists["lead_time_bucket"], legacy_series_exists["series_reference_rate"], color="#e76f51")
        axes[1].set_ylim(0, 1)
    axes[1].set_title("Series co reference approved, bucket legacy (RAW)")
    for ax in axes:
        ax.set_xlabel("lead-time bucket (ngay)")
    fig.tight_layout()
    return fig


def _numeric_bars(ax, df: pd.DataFrame, x_col: str, *, color: str, width: float) -> None:
    """Cot tren truc so THAT (khong phai 1 nhan chuoi/cot): tick do `MaxNLocator` tu chon nen khong chong nhan khi co hang chuc gia tri x
    (vd median gap 0, 0.5, 1, ... 28 - ban cu ve nhan chuoi nen chu de len nhau, khong doc duoc)."""
    ax.bar(df[x_col].astype(float), df["n_series"], width=width, color=color)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=10, integer=True))


def plot_series_turnover(by_days: pd.DataFrame, max_gap: pd.DataFrame, median_gap: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    if by_days.empty:
        _no_data(axes[0])
    else:
        _numeric_bars(axes[0], by_days, "n_observed_days", color="#457b9d", width=0.8)
    axes[0].set_title("Canonical series theo so ngay observed")
    axes[0].set_xlabel("n_observed_days")
    axes[0].set_ylabel("so series")
    if max_gap.empty:
        _no_data(axes[1])
    else:
        _numeric_bars(axes[1], max_gap, "max_gap_days", color="#2a9d8f", width=0.8)
    axes[1].set_title("Canonical series theo max_gap_days")
    axes[1].set_xlabel("max_gap_days (0 = series 1 ngay; 1 = lien tuc)")
    if median_gap.empty:
        _no_data(axes[2])
    else:
        _numeric_bars(axes[2], median_gap, "median_gap_days", color="#e9c46a", width=0.4)  # median cua khoang cach nguyen: buoc 0.5
    axes[2].set_title("Canonical series theo median_gap_days")
    axes[2].set_xlabel("median_gap_days (0 = series 1 ngay)")
    fig.tight_layout()
    return fig


def plot_history_length(hist: pd.DataFrame):
    fig, ax = _fig((8, 4.5))
    order = ["1", "2", "3-6", "7-13", "14-29", "30+"]
    if hist.empty:
        _no_data(ax)
    else:
        pivot = hist.pivot_table(index="history_days_bucket", columns="city", values="n_series", aggfunc="sum", fill_value=0)
        pivot = pivot.reindex([b for b in order if b in pivot.index])
        pivot.plot(kind="bar", stacked=True, ax=ax, width=0.8)
        ax.tick_params(axis="x", rotation=0)
    ax.set_xlabel("so ngay crawl co snapshot success cua (hotel, check-in)")
    ax.set_ylabel("so series (hotel, check-in)")
    ax.set_title("Do dai lich su theo (hotel, check-in), MAIN")
    fig.tight_layout()
    return fig


def plot_readiness_by_horizon(readiness: pd.DataFrame):
    fig, ax = _fig((7, 4.5))
    if readiness.empty:
        _no_data(ax)
    else:
        ax.bar(readiness["horizon_days"].astype(str), readiness["theoretical_date_pairs"], color="#457b9d")
    ax.set_xlabel("horizon (ngay)")
    ax.set_ylabel("theoretical_date_pairs")
    ax.set_title("Cap ngay quan sat cach DUNG K ngay (ly thuyet, chua phai label causal)")
    fig.tight_layout()
    return fig
