# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Pure-function tests for the ScreenLiveMonitor de-duplication helpers.

Texts are taken from the 2026-10-13 production capture analysis
(~/.claude/work/minecontext/next/capture-analysis-report.md); personal data
such as e-mail addresses is left out.
"""

import datetime

from opencontext.screen_record.live_monitor import (
    _dedupe_repeated_sentences,
    _is_loading_only,
    _jaccard3,
    _should_extend_activity,
    _should_suppress_summary,
)

# Report A1: the Live transcript repeated its first half verbatim.
A1_HALF = (
    "ユーザーは「MineContext」の管理画面で、今日の日報や日々の活動ログを確認していました。"
    "画面の下部には、カレンダーや今日、日報などのナビゲーションアイコンが並んでおり、"
    "様々なメニューを切り替えて情報を閲覧していました。"
    "ページをスクロールして作業内容やAIの活動記録を詳細に確認する操作を行っていました。"
)

# Report A3/A4: the same 30-second interval re-described (jac 0.72).
A3 = (
    "ユーザーはMicrosoft Edgeでさくらインターネットのサポートページを開き、"
    "サーバーパスワードの変更手順を確認していました。"
    "ページの内容を一通り確認した後、ブラウザのウィンドウを小さくして、"
    "画面監視を行っている「MineContext」のアプリケーション画面を表示させていました。"
)
A4 = (
    "ユーザーはMicrosoft Edgeでさくらインターネットのサポートページを開き、"
    "サーバーパスワードの変更手順を確認していました。"
    "ページの内容を一通り確認した後、ユーザーはブラウザのウィンドウを縮小させ、"
    "画面監視アプリケーションである「MineContext」のウィンドウを前面に表示させていました。"
)

# Report A5 (e-mail masked out): an unrelated activity text.
A5 = (
    "ユーザーはMicrosoft Edgeで「さくらのレンタルサーバ」のウェブメールを開き、"
    "受信トレイの内容を確認していました。"
    "画面にはこのアカウントで受信した複数のメールが表示されていました。"
    "ユーザーは特定のメールを選択して、その詳細やプレビューを確認していました。"
)

UNRELATED = "東京駅から新幹線に乗って、大阪のオフィスまで移動していました。"

# Report S3/S4 shape: the same screen re-transcribed 46 s later
# (only the heading and one menu line differ).
SUMMARY_A = "\n".join([
    "# さくらのレンタルサーバ メールアドレス一覧画面",
    "メールアドレス - さくらのレンタルサーバ コントロールパネル",
    "ホーム",
    "メール",
    "サーバー情報",
    "ビジネスソリューション",
    "サポート",
    "スタートアップガイド",
    "よくある質問",
    "メールアドレス",
    "ドメインのメールアドレスを作成できます",
])
SUMMARY_B = "\n".join([
    "# さくらのレンタルサーバ メールアドレス管理画面",
    "メールアドレス - さくらのレンタルサーバ コントロールパネル",
    "ホーム",
    "メール",
    "サーバー情報",
    "メニュー一覧",
    "サポート",
    "スタートアップガイド",
    "よくある質問",
    "メールアドレス",
    "ドメインのメールアドレスを作成できます",
])

BASE_END = datetime.datetime(2026, 10, 12, 23, 28, 29)
EDGE = "Microsoft Edge"
SUPPORT_TITLE = "メールアドレス - さくらのレンタルサーバ コントロールパネル"


# -- _dedupe_repeated_sentences ---------------------------------------------------

def test_dedupe_whole_text_repetition_a1():
    assert _dedupe_repeated_sentences(A1_HALF + A1_HALF) == A1_HALF


def test_dedupe_no_repetition_unchanged():
    assert _dedupe_repeated_sentences(A5) == A5


def test_dedupe_consecutive_repetition():
    once = "設定画面で同期のオンオフを切り替えていました。"
    assert _dedupe_repeated_sentences(once + once) == once
    assert _dedupe_repeated_sentences(once * 3) == once


# -- _jaccard3 ----------------------------------------------------------------------

def test_jaccard3_identical():
    assert _jaccard3(A3, A3) == 1.0
    lines = ["受信トレイを開いてメールの一覧を確認していました。",
             "添付ファイルをダウンロードしました。",
             "返信の下書きを書いていました。"]
    # whitespace/newline differences are normalized away
    assert _jaccard3("\n".join(lines), "\n\n".join(lines)) == 1.0


def test_jaccard3_unrelated():
    assert _jaccard3(A3, UNRELATED) < 0.2


def test_jaccard3_paraphrase_pair():
    assert _jaccard3(A3, A4) >= 0.6
    assert _jaccard3(SUMMARY_A, SUMMARY_B) >= 0.6


# -- activity merge decision ---------------------------------------------------------

def test_merge_same_window_similar_text():
    assert _should_extend_activity(
        A3, EDGE, SUPPORT_TITLE, BASE_END,
        EDGE, SUPPORT_TITLE, A4, BASE_END + datetime.timedelta(seconds=31))


def test_merge_different_title_creates_new_record():
    assert not _should_extend_activity(
        A3, EDGE, SUPPORT_TITLE, BASE_END,
        EDGE, "MineContext", A4, BASE_END + datetime.timedelta(seconds=31))


def test_merge_dissimilar_text_creates_new_record():
    assert not _should_extend_activity(
        A3, EDGE, SUPPORT_TITLE, BASE_END,
        EDGE, SUPPORT_TITLE, UNRELATED, BASE_END + datetime.timedelta(seconds=31))


def test_merge_over_ten_minutes_creates_new_record():
    assert not _should_extend_activity(
        A3, EDGE, SUPPORT_TITLE, BASE_END,
        EDGE, SUPPORT_TITLE, A4, BASE_END + datetime.timedelta(minutes=11))


def test_merge_exactly_ten_minutes_still_merges():
    assert _should_extend_activity(
        A3, EDGE, SUPPORT_TITLE, BASE_END,
        EDGE, SUPPORT_TITLE, A4, BASE_END + datetime.timedelta(minutes=10))


# -- screen_summary suppression --------------------------------------------------------

def test_summary_suppressed_within_five_minutes():
    # S3/S4: same screen re-transcribed 46 s later
    assert _should_suppress_summary(1000.0, SUMMARY_A, 1000.0 + 46.0, SUMMARY_B)


def test_summary_saved_after_five_minutes():
    assert not _should_suppress_summary(1000.0, SUMMARY_A, 1000.0 + 400.0, SUMMARY_B)


def test_summary_saved_without_previous_or_when_dissimilar():
    assert not _should_suppress_summary(None, "", 1000.0, SUMMARY_A)
    assert not _should_suppress_summary(1000.0, SUMMARY_A, 1000.0 + 46.0, A3)


# -- loading-only transcripts -----------------------------------------------------------

def test_loading_only():
    assert _is_loading_only("読み込み中")
    assert _is_loading_only("読み込み中\n読み込み中\n")
    assert not _is_loading_only("# ウェブメール\n受信トレイ")
    assert not _is_loading_only("")
