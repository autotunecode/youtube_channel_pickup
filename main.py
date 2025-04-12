import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta, timezone
import isodate # APIからの期間文字列をパースするために必要になる場合がある (今回は直接使わないが関連ライブラリとして)
from dateutil.parser import isoparse # ISO 8601形式の日付文字列をパース

# --- 定数 ---
# 検索する動画の最大数 (APIクォータに影響)
MAX_VIDEO_RESULTS_PER_PAGE = 50 # 1ページあたりの最大値
MAX_TOTAL_VIDEO_RESULTS = 100 # 複数ページを取得する場合の合計最大値 (例: 50 * 2ページ)
# 情報を取得するチャンネルの最大数 (APIクォータに影響)
MAX_CHANNELS_TO_FETCH_PER_CALL = 50 # 1回のAPI呼び出しで取得できるチャンネル数の上限
# 新規チャンネルとみなす期間（日数）
NEW_CHANNEL_DAYS_THRESHOLD = 30

# --- Streamlit UI 設定 ---
st.set_page_config(page_title="YouTube新規急成長チャンネル調査", layout="wide")
st.title("🚀 YouTube新規急成長チャンネル調査ツール")
st.caption("直近1ヶ月以内に開設され、勢いのある可能性のあるチャンネルを探します。")

st.sidebar.header("設定")
api_key = st.sidebar.text_input("YouTube Data API v3 キー", type="password", help="Google Cloud Platformから取得したAPIキーを入力してください。")

# 検索範囲の設定を追加
st.sidebar.subheader("検索範囲の設定")
max_videos = st.sidebar.slider(
    "検索する動画の最大数",
    min_value=50,
    max_value=500,
    value=100,
    step=50,
    help="検索する動画の数を設定します。数が多いほど時間がかかります。"
)

new_channel_days = st.sidebar.slider(
    "新規チャンネルとみなす期間（日数）",
    min_value=7,
    max_value=90,
    value=30,
    step=1,
    help="この日数以内に開設されたチャンネルを新規チャンネルとして扱います。"
)

st.sidebar.markdown("""
**APIキーの取得方法:**
1. [Google Cloud Console](https://console.cloud.google.com/)にアクセスします。
2. 新しいプロジェクトを作成するか、既存のプロジェクトを選択します。
3. 「APIとサービス」 > 「ライブラリ」に移動します。
4. 「YouTube Data API v3」を検索し、有効にします。
5. 「APIとサービス」 > 「認証情報」に移動します。
6. 「認証情報を作成」 > 「APIキー」を選択してキーを作成します。
7. **重要:** APIキーの不正利用を防ぐため、適切な制限（HTTPリファラー、IPアドレスなど）を設定することを強く推奨します。
""")

# 検索期間の計算
today = datetime.now(timezone.utc)
one_month_ago = today - timedelta(days=new_channel_days)
one_month_ago_iso = one_month_ago.isoformat()

# --- API関連関数 ---

def get_youtube_service(api_key_input):
    """YouTube APIサービスオブジェクトを構築する"""
    if not api_key_input:
        st.warning("APIキーを入力してください。")
        return None
    try:
        # disable_oauth_warning=True はローカルスクリプトや非Webアプリでよく使われます
        # 本番環境のWebアプリではより安全な認証方法を検討してください
        youtube = build('youtube', 'v3', developerKey=api_key_input, cache_discovery=False)
        return youtube
    except HttpError as e:
        st.error(f"APIキーの検証中にエラーが発生しました: {e}")
        st.error("有効なAPIキーか、またはAPIキーの制限を確認してください。")
        return None
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
        return None

def search_popular_videos(youtube, published_after_iso, max_results=50):
    """指定期間後に公開された人気の動画を検索する"""
    video_ids = []
    channel_ids = set() # 重複を避けるためセットを使用
    next_page_token = None
    results_fetched = 0

    st.write(f"{NEW_CHANNEL_DAYS_THRESHOLD}日以内に公開された人気動画を検索中...")

    try:
        while results_fetched < max_results:
            num_to_fetch = min(MAX_VIDEO_RESULTS_PER_PAGE, max_results - results_fetched)
            search_response = youtube.search().list(
                q='', # 特定のキーワードなしで検索（人気順で取得するため）
                part='snippet',
                type='video',
                order='viewCount', # 再生回数順
                publishedAfter=published_after_iso,
                maxResults=num_to_fetch,
                pageToken=next_page_token
            ).execute()

            for item in search_response.get('items', []):
                video_ids.append(item['id']['videoId'])
                channel_ids.add(item['snippet']['channelId'])
                results_fetched += 1
                if results_fetched >= max_results:
                    break

            next_page_token = search_response.get('nextPageToken')
            if not next_page_token or results_fetched >= max_results:
                break # 次のページがないか、最大取得数に達したら終了

        st.write(f"約{len(channel_ids)}個のユニークなチャンネルIDを持つ人気動画が見つかりました。")
        return list(channel_ids) # セットをリストに変換して返す

    except HttpError as e:
        st.error(f"動画検索中にAPIエラーが発生しました: {e}")
        st.error("APIクォータの上限に達したか、APIキーに問題がある可能性があります。")
        return []
    except Exception as e:
        st.error(f"動画検索中に予期せぬエラーが発生しました: {e}")
        return []

def get_channel_details(youtube, channel_ids):
    """複数のチャンネルIDから詳細情報を取得する"""
    channel_data = []
    # APIは一度に最大50個のIDしか受け付けないため、分割して処理
    for i in range(0, len(channel_ids), MAX_CHANNELS_TO_FETCH_PER_CALL):
        batch_ids = channel_ids[i:i + MAX_CHANNELS_TO_FETCH_PER_CALL]
        st.write(f"チャンネル情報を取得中... ({i+1}-{min(i+len(batch_ids), len(channel_ids))} / {len(channel_ids)})")
        try:
            channel_response = youtube.channels().list(
                part='snippet,statistics,contentDetails', # snippet: 基本情報, statistics: 統計情報, contentDetails: uploadsプレイリストIDなど
                id=','.join(batch_ids)
            ).execute()

            for item in channel_response.get('items', []):
                snippet = item.get('snippet', {})
                statistics = item.get('statistics', {})

                # 日付文字列をdatetimeオブジェクトに変換 (タイムゾーン情報も考慮)
                published_at_str = snippet.get('publishedAt')
                published_at_dt = None
                if published_at_str:
                    try:
                        published_at_dt = isoparse(published_at_str)
                    except ValueError:
                        st.warning(f"日付形式の解析エラー: {published_at_str} (Channel ID: {item.get('id')})")
                        # 解析できない場合はスキップするか、Noneのままにする
                        continue # または published_at_dt = None

                # 登録者数は非公開の場合がある
                subscriber_count = statistics.get('subscriberCount')
                subscriber_count_int = int(subscriber_count) if subscriber_count else 0 # 非公開なら0として扱う

                channel_data.append({
                    'channel_id': item.get('id'),
                    'channel_name': snippet.get('title'),
                    'description': snippet.get('description'),
                    'published_at_str': published_at_str, # 文字列も保持
                    'published_at': published_at_dt,     # datetimeオブジェクト
                    'subscriber_count': subscriber_count_int,
                    'view_count': int(statistics.get('viewCount', 0)),
                    'video_count': int(statistics.get('videoCount', 0)),
                    'hidden_subscriber_count': statistics.get('hiddenSubscriberCount', False),
                })
        except HttpError as e:
            st.error(f"チャンネル情報取得中にAPIエラーが発生しました (IDバッチ {i+1}-{i+len(batch_ids)}): {e}")
            st.error("APIクォータの上限、または無効なチャンネルIDが含まれている可能性があります。")
            # エラーが発生しても、次のバッチの処理を試みる (continue)
            continue
        except Exception as e:
            st.error(f"チャンネル情報取得中に予期せぬエラーが発生しました: {e}")
            continue # 次のバッチへ

    return channel_data

# --- メイン処理 ---
if api_key:
    youtube_service = get_youtube_service(api_key)

    if youtube_service:
        if st.button("📈 調査開始", key="start_search"):
            with st.spinner("調査を実行中... (数分かかる場合があります)"):
                # 1. 人気動画を検索してチャンネルIDリストを取得
                channel_ids_to_check = search_popular_videos(youtube_service, one_month_ago_iso, max_videos)

                if not channel_ids_to_check:
                    st.warning("調査対象となるチャンネルが見つかりませんでした。")
                else:
                    # 2. チャンネル詳細情報を取得
                    all_channel_details = get_channel_details(youtube_service, channel_ids_to_check)

                    if not all_channel_details:
                        st.warning("チャンネル情報の取得に失敗しました。")
                    else:
                        # 3. データフレームに変換し、新規チャンネルをフィルタリング
                        df = pd.DataFrame(all_channel_details)

                        # published_at が None でない行のみを対象にする
                        df_filtered = df.dropna(subset=['published_at']).copy()

                        # 新規チャンネルをフィルタリング (タイムゾーン対応で比較)
                        df_filtered['is_new'] = df_filtered['published_at'] >= one_month_ago
                        new_channels_df = df_filtered[df_filtered['is_new']].copy()

                        st.success(f"調査完了！ {len(new_channels_df)} 個の新規チャンネルが見つかりました。")

                        if not new_channels_df.empty:
                            # 表示用に列を整理・追加
                            new_channels_df['channel_link'] = new_channels_df['channel_id'].apply(lambda x: f"https://www.youtube.com/channel/{x}")
                            # 日付を読みやすい形式に変換 (タイムゾーン情報を削除して表示)
                            new_channels_df['開設日'] = new_channels_df['published_at'].dt.tz_localize(None).dt.strftime('%Y-%m-%d %H:%M')
                            new_channels_df['登録者数'] = new_channels_df.apply(
                                lambda row: f"{row['subscriber_count']:,}" if not row['hidden_subscriber_count'] else "非公開",
                                axis=1
                            )
                            new_channels_df['総再生回数'] = new_channels_df['view_count'].apply(lambda x: f"{x:,}")
                            new_channels_df['動画数'] = new_channels_df['video_count'].apply(lambda x: f"{x:,}")

                            # 表示する列を選択し、並び替え (登録者数で降順ソート)
                            # 'subscriber_count' は int なのでソートに使える
                            display_df = new_channels_df.sort_values(by='subscriber_count', ascending=False)

                            display_columns = {
                                'channel_name': 'チャンネル名',
                                '開設日': '開設日',
                                '登録者数': '登録者数',
                                '総再生回数': '総再生回数',
                                '動画数': '動画数',
                                'channel_link': 'チャンネルリンク'
                                # 'description': '概要' # 必要であれば追加
                            }
                            st.dataframe(display_df[list(display_columns.keys())].rename(columns=display_columns),
                                         hide_index=True,
                                         column_config={
                                            "チャンネルリンク": st.column_config.LinkColumn("リンク", display_text="開く")
                                         })

                            # CSVダウンロードボタン
                            @st.cache_data # データが変わらない限りキャッシュ
                            def convert_df_to_csv(df_to_convert):
                                return df_to_convert.to_csv(index=False).encode('utf-8-sig') # BOM付きUTF-8

                            csv = convert_df_to_csv(display_df[list(display_columns.keys())].rename(columns=display_columns))
                            st.download_button(
                                label="📥 結果をCSVでダウンロード",
                                data=csv,
                                file_name=f"youtube_new_channels_{today.strftime('%Y%m%d')}.csv",
                                mime='text/csv',
                            )

                        else:
                            st.info(f"直近{NEW_CHANNEL_DAYS_THRESHOLD}日以内に開設されたチャンネルは、今回の検索範囲（人気動画上位{MAX_TOTAL_VIDEO_RESULTS}件由来）には見つかりませんでした。")
        else:
            st.info("APIキーを入力し、「調査開始」ボタンを押してください。")

else:
    st.info("サイドバーからYouTube Data API v3 キーを入力してください。")

st.markdown("---")
st.caption("注意: APIのクォータ制限、チャンネル設定（登録者非公開など）、検索範囲の限界により、すべての新規急成長チャンネルを網羅できるわけではありません。")
