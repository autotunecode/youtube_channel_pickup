import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta, timezone
import isodate # APIからの期間文字列をパースするために必要になる場合がある (今回は直接使わないが関連ライブラリとして)
from dateutil.parser import isoparse # ISO 8601形式の日付文字列をパース
from langdetect import detect, LangDetectException # 言語判定ライブラリ

# --- 定数 ---
# 検索する動画の最大数 (APIクォータに影響)
MAX_VIDEO_RESULTS_PER_PAGE = 50 # 1ページあたりの最大値
MAX_TOTAL_VIDEO_RESULTS = 100 # 複数ページを取得する場合の合計最大値 (例: 50 * 2ページ)
# 情報を取得するチャンネルの最大数 (APIクォータに影響)
MAX_CHANNELS_TO_FETCH_PER_CALL = 50 # 1回のAPI呼び出しで取得できるチャンネル数の上限
# 新規チャンネルとみなす期間（日数）
NEW_CHANNEL_DAYS_THRESHOLD = 30

# 地域ごとの検索用シードキーワード (現地語で検索することで精度を上げる)
REGION_SEED_KEYWORDS = {
    "JP": ["の", "動画", "チャンネル", "vlog", "ゲーム", "日本", "公式", "配信"],
    "US": ["the", "a", "my", "video", "official", "vlog", "game", "2024", "2025"],
    "GB": ["the", "uk", "british", "london", "vlog", "football", "official"],
    "CA": ["the", "canada", "vlog", "toronto", "official", "hockey"],
    "AU": ["the", "australia", "vlog", "sydney", "official", "cricket"],
    "KR": ["이", "영상", "채널", "브이로그", "공식", "게임", "TV"],
    "TW": ["的", "影片", "頻道", "官方", "遊戲", "vlog", "直播"],
    "CN": ["的", "视频", "频道", "官方", "游戏", "vlog", "直播"],  # 補足
    "IN": ["the", "video", "song", "movie", "new", "official", "hindi", "songs"],
    "DE": ["der", "die", "das", "und", "video", "offiziell", "vlog"],
    "FR": ["le", "la", "les", "et", "vidéo", "officiel", "vlog"],
    "BR": ["o", "a", "video", "oficial", "brasil", "vlog", "jogo"],
    "MX": ["el", "la", "video", "oficial", "mexico", "vlog", "juego"],
    "RU": ["и", "в", "не", "на", "видео", "официальный", "влог"],
    "ES": ["el", "la", "y", "video", "oficial", "vlog", "juego"],
}

# デフォルトの検索クエリ
DEFAULT_SEARCH_QUERIES = ['a', 'the', 'how', 'what', 'music', 'game', 'vlog', 'tutorial', 'review', 'news']

# --- Streamlit UI 設定 ---
st.set_page_config(page_title="YouTube分析ツール", layout="wide")

# サイドバーの設定
st.sidebar.header("設定")
api_key = st.sidebar.text_input("YouTube Data API v3 キー", type="password", help="Google Cloud Platformから取得したAPIキーを入力してください。")

# 機能選択メニュー
analysis_mode = st.sidebar.selectbox(
    "分析モード",
    ["新規急成長チャンネル", "チャンネル内再生ランキング"],
    help="実行したい分析モードを選択してください"
)

# デフォルトの新規チャンネル期間を設定
new_channel_days = 30

# 新規急成長チャンネル用の設定
if analysis_mode == "新規急成長チャンネル":
    st.title("🚀 YouTube新規急成長チャンネル調査ツール")
    st.caption("直近1ヶ月以内に開設され、勢いのある可能性のあるチャンネルを探します。")
    
    st.sidebar.subheader("新規チャンネル検索設定")
    
    # 検索対象国の選択（地域コードと言語コードのマッピング）
    region_options = {
        "すべての国": {"region": None, "language": None},
        "日本 (JP)": {"region": "JP", "language": "ja"},
        "アメリカ (US)": {"region": "US", "language": "en"},
        "イギリス (GB)": {"region": "GB", "language": "en"},
        "カナダ (CA)": {"region": "CA", "language": "en"},
        "オーストラリア (AU)": {"region": "AU", "language": "en"},
        "ドイツ (DE)": {"region": "DE", "language": "de"},
        "フランス (FR)": {"region": "FR", "language": "fr"},
        "韓国 (KR)": {"region": "KR", "language": "ko"},
        "台湾 (TW)": {"region": "TW", "language": "zh-TW"},
        "インド (IN)": {"region": "IN", "language": "hi"},
        "ブラジル (BR)": {"region": "BR", "language": "pt"},
        "メキシコ (MX)": {"region": "MX", "language": "es"}
    }
    
    selected_region_name = st.sidebar.selectbox(
        "検索対象国",
        options=list(region_options.keys()),
        index=1,  # デフォルトは日本
        help="特定の国のコンテンツを優先的に検索します。地域と言語の両方でフィルタリングされます。注意: YouTube APIの仕様上、完全に特定の国のチャンネルのみに限定することはできません。"
    )
    region_code = region_options[selected_region_name]["region"]
    language_code = region_options[selected_region_name]["language"]
    
    # ジャンル（カテゴリ）の選択
    category_options = {
        "すべてのジャンル": None,
        "🎬 映画とアニメーション": "1",
        "🚗 自動車と乗り物": "2",
        "🎵 音楽": "10",
        "🐾 ペットと動物": "15",
        "⚽ スポーツ": "17",
        "🎮 ゲーム": "20",
        "📝 ブログ": "22",
        "🎭 コメディ": "23",
        "🎓 教育": "27",
        "🔧 ハウツーとスタイル": "26",
        "👥 人物とブログ": "22",
        "🌍 旅行とイベント": "19",
        "📰 ニュースと政治": "25",
        "🎨 エンターテイメント": "24",
        "🔬 科学と技術": "28",
        "🍳 料理": "26"
    }
    
    selected_category_name = st.sidebar.selectbox(
        "検索ジャンル",
        options=list(category_options.keys()),
        index=0,  # デフォルトはすべてのジャンル
        help="特定のジャンルの動画のみを検索します。「すべてのジャンル」を選択すると、ジャンル制限なしで検索します。"
    )
    category_id = category_options[selected_category_name]
    
    max_videos = st.sidebar.slider(
        "検索する動画の最大数",
        min_value=50,
        max_value=2000,
        value=300,
        step=50,
        help="検索する動画の数を設定します。数が多いほど新規チャンネルが見つかりやすくなりますが、時間がかかります。"
    )

    new_channel_days = st.sidebar.slider(
        "新規チャンネルとみなす期間（日数）",
        min_value=7,
        max_value=365,
        value=60,
        step=1,
        help="この日数以内に開設されたチャンネルを新規チャンネルとして扱います。期間を長くすると見つかりやすくなります。"
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("詳細検索設定")

    search_keyword = st.sidebar.text_input(
        "検索キーワード (任意)",
        help="特定のトピックに関連するチャンネルを探す場合に入力してください。空欄の場合はランダムなキーワードで広く検索します。"
    )

    search_order = st.sidebar.selectbox(
        "検索順序",
        options=["date", "relevance", "viewCount", "rating", "title", "videoCount"],
        index=0,
        format_func=lambda x: {
            "date": "アップロード日 (date)",
            "relevance": "関連度 (relevance)",
            "viewCount": "再生回数 (viewCount)",
            "rating": "評価 (rating)",
            "title": "タイトル (title)",
            "videoCount": "動画数 (videoCount)"
        }[x],
        help="動画検索APIのソート順序を指定します。デフォルトは「アップロード日」で、新しい動画を見つけやすくしています。"
    )

    video_duration = st.sidebar.selectbox(
        "動画の長さ",
        options=["any", "short", "medium", "long"],
        index=0,
        format_func=lambda x: {
            "any": "指定なし",
            "short": "短い (4分未満)",
            "medium": "中くらい (4分〜20分)",
            "long": "長い (20分以上)"
        }[x],
        help="検索する動画の長さを指定します。"
    )

    strict_country_filter = st.sidebar.checkbox(
        "チャンネルの登録国で厳密にフィルタリング",
        value=False,
        help="検索対象国で選択した国と、チャンネルの登録国設定が一致するものだけを表示します。※チャンネルが国を設定していない場合は除外される可能性があります。"
    )

else:
    st.title("📊 YouTubeチャンネル内再生ランキング")
    st.caption("指定したチャンネルの動画再生数ランキングを表示します。")
    
    st.sidebar.subheader("ランキング表示設定")
    channel_id = st.sidebar.text_input(
        "チャンネルID",
        help="分析したいチャンネルのIDを入力してください（例：UCxxxxxxxxxxxxxxxxxxxxxxxx）"
    )
    
    ranking_limit = st.sidebar.slider(
        "表示する動画数",
        min_value=10,
        max_value=100,
        value=50,
        step=10,
        help="ランキングに表示する動画の数を設定します"
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

def detect_channel_language(text):
    """テキストから言語を判定する"""
    if not text or len(text) < 3: # 短すぎるテキストは判定不可
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None

def search_popular_videos(youtube, published_after_iso, max_results=50, region_code=None, language_code=None, category_id=None, search_keyword=None, order='date', video_duration='any'):
    """指定期間後に公開された人気の動画を検索する"""
    video_ids = []
    channel_ids = set() # 重複を避けるためセットを使用
    next_page_token = None
    results_fetched = 0

    region_text = f" ({region_code})" if region_code else " (全世界)"
    st.write(f"{NEW_CHANNEL_DAYS_THRESHOLD}日以内に公開された人気動画を検索中...{region_text}")
    st.write(f"🔍 検索条件: publishedAfter={published_after_iso}, Order={order}")
    if category_id:
        st.write(f"🎯 ジャンルフィルター: カテゴリID {category_id}")
    if search_keyword:
        st.write(f"🔑 キーワード: {search_keyword}")

    try:
        while results_fetched < max_results:
            num_to_fetch = min(MAX_VIDEO_RESULTS_PER_PAGE, max_results - results_fetched)
            st.write(f"📡 API呼び出し中... (取得予定: {num_to_fetch}件)")
            
            # 検索クエリの決定
            if search_keyword:
                current_query = search_keyword
            else:
                # 検索クエリの選択 (指定された地域に応じてシードキーワードを変更)
                if region_code and region_code in REGION_SEED_KEYWORDS:
                    search_queries = REGION_SEED_KEYWORDS[region_code]
                else:
                    search_queries = DEFAULT_SEARCH_QUERIES
                
                query_index = (results_fetched // MAX_VIDEO_RESULTS_PER_PAGE) % len(search_queries)
                current_query = search_queries[query_index]
            
            # API呼び出しパラメータを構築
            search_params = {
                'q': current_query,
                'part': 'snippet',
                'type': 'video',
                'order': order,
                'publishedAfter': published_after_iso,
                'maxResults': num_to_fetch,
                'pageToken': next_page_token,
                'videoDuration': video_duration,
                'videoDefinition': 'any'
            }
            
            # region_codeがNoneでない場合のみ追加
            if region_code:
                search_params['regionCode'] = region_code
            
            # language_codeがNoneでない場合のみ追加
            if language_code:
                search_params['relevanceLanguage'] = language_code
            
            # category_idがNoneでない場合のみ追加
            if category_id:
                search_params['videoCategoryId'] = category_id
            
            search_response = youtube.search().list(**search_params).execute()

            items_count = len(search_response.get('items', []))
            st.write(f"✅ API応答: {items_count}件の動画を取得")
            
            if items_count == 0:
                st.warning("⚠️ APIから動画が返されませんでした。検索条件を確認してください。")
                break

            for item in search_response.get('items', []):
                video_ids.append(item['id']['videoId'])
                channel_ids.add(item['snippet']['channelId'])
                results_fetched += 1
                if results_fetched >= max_results:
                    break

            next_page_token = search_response.get('nextPageToken')
            st.write(f"📊 現在の取得数: {results_fetched}件, ユニークチャンネル数: {len(channel_ids)}個")
            
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

                # 言語判定 (タイトル + 説明文)
                full_text = f"{snippet.get('title', '')} {snippet.get('description', '')}"
                detected_lang = detect_channel_language(full_text)

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
                    'country': snippet.get('country', 'Unknown'), # 既存: APIのcountry
                    'detected_lang': detected_lang # 追加: テキストからの判定言語
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

def get_channel_videos(youtube, channel_id, max_results=50):
    """指定したチャンネルの動画を取得する"""
    videos = []
    next_page_token = None
    
    try:
        # まずチャンネルの情報を取得してアップロードプレイリストIDを取得
        channel_response = youtube.channels().list(
            part='contentDetails',
            id=channel_id
        ).execute()
        
        if not channel_response.get('items'):
            st.error("指定されたチャンネルIDが見つかりませんでした。")
            return []
            
        # アップロードプレイリストIDを取得
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        # プレイリストから動画を取得
        while True:
            playlist_response = youtube.playlistItems().list(
                part='snippet',
                playlistId=uploads_playlist_id,
                maxResults=50,  # APIの制限により50が最大
                pageToken=next_page_token
            ).execute()
            
            video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_response['items']]
            
            # 動画の詳細情報を取得
            video_response = youtube.videos().list(
                part='snippet,statistics',
                id=','.join(video_ids)
            ).execute()
            
            for video in video_response['items']:
                videos.append({
                    'video_id': video['id'],
                    'title': video['snippet']['title'],
                    'published_at': video['snippet']['publishedAt'],
                    'view_count': int(video['statistics'].get('viewCount', 0)),
                    'like_count': int(video['statistics'].get('likeCount', 0)),
                    'comment_count': int(video['statistics'].get('commentCount', 0))
                })
                
                if len(videos) >= max_results:
                    return videos
                    
            next_page_token = playlist_response.get('nextPageToken')
            if not next_page_token:
                break
                
        return videos
        
    except HttpError as e:
        st.error(f"動画情報の取得中にAPIエラーが発生しました: {e}")
        return []
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
        return []

# --- メイン処理 ---
if api_key:
    youtube_service = get_youtube_service(api_key)

    if youtube_service:
        if analysis_mode == "新規急成長チャンネル":
            if st.button("📈 調査開始", key="start_search"):
                with st.spinner("調査を実行中... (数分かかる場合があります)"):
                    # 1. 人気動画を検索してチャンネルIDリストを取得
                    channel_ids_to_check = search_popular_videos(
                        youtube_service, 
                        one_month_ago_iso, 
                        max_videos, 
                        region_code, 
                        language_code, 
                        category_id,
                        search_keyword,
                        search_order,
                        video_duration
                    )

                    if not channel_ids_to_check:
                        st.warning("調査対象となるチャンネルが見つかりませんでした。")
                    else:
                        # 2. チャンネル詳細情報を取得
                        all_channel_details = get_channel_details(youtube_service, channel_ids_to_check)

                        if not all_channel_details:
                            st.warning("チャンネル情報の取得に失敗しました。")
                        else:
                            # 3. データフレームに変換
                            df = pd.DataFrame(all_channel_details)

                            # published_at が None でない行のみを対象にする
                            df_filtered = df.dropna(subset=['published_at']).copy()

                            # 厳密な国フィルタリング
                            if strict_country_filter and region_code:
                                before_count = len(df_filtered)
                                
                                # 国が一致するか、または（国が設定されておらず、かつ検出言語が一致する）場合のみ残す
                                # language_code は "ja", "en", "ko" など
                                def is_valid_country_or_language(row):
                                    country = row.get('country')
                                    detected_lang = row.get('detected_lang')
                                    
                                    # 1. 国設定が一致する場合
                                    if country == region_code:
                                        return True
                                    
                                    # 2. 国設定がない（またはUnknown）場合、言語で判定
                                    # 注意: detected_lang は 'ja' のように返る。language_codeと比較
                                    if (not country or country == 'Unknown') and detected_lang == language_code:
                                        return True
                                        
                                    return False

                                df_filtered = df_filtered[df_filtered.apply(is_valid_country_or_language, axis=1)]
                                after_count = len(df_filtered)
                                st.write(f"🛑 国フィルタリング (厳密モード): {before_count} -> {after_count} 件 (対象国: {region_code}, 対象言語: {language_code})")

                            # 新規チャンネルをフィルタリング (タイムゾーン対応で比較)
                            df_filtered['is_new'] = df_filtered['published_at'] >= one_month_ago
                            new_channels_df = df_filtered[df_filtered['is_new']].copy()

                            if not new_channels_df.empty:
                                st.success(f"調査完了！ {len(new_channels_df)} 個の新規チャンネルが見つかりました。")

                                # --- 要因分析計算 ---
                                new_channels_df['avg_views_per_video'] = new_channels_df['view_count'] / new_channels_df['video_count'].replace(0, 1)
                                
                                # 登録者数が0または非公開の場合は1として計算（ゼロ除算防止）
                                safe_sub_count = new_channels_df['subscriber_count'].replace(0, 1)
                                new_channels_df['views_per_sub_ratio'] = new_channels_df['view_count'] / safe_sub_count
                                
                                # 経過日数
                                new_channels_df['days_active'] = (today - new_channels_df['published_at']).dt.days.replace(0, 1)
                                new_channels_df['estimated_daily_views'] = new_channels_df['view_count'] / new_channels_df['days_active']

                                # 成長タイプ判定
                                def classify_growth(row):
                                    tags = []
                                    if row['views_per_sub_ratio'] > 50:
                                        tags.append("高いエンゲージメント")
                                    if row['estimated_daily_views'] > 1000: # 閾値は調整可能
                                        tags.append("バズり中")
                                    if row['video_count'] > row['days_active']:
                                        tags.append("高頻度投稿")
                                    
                                    return ", ".join(tags) if tags else "通常成長"

                                new_channels_df['growth_factors'] = new_channels_df.apply(classify_growth, axis=1)

                                # 表示用に列を整理・追加
                                new_channels_df['channel_link'] = new_channels_df['channel_id'].apply(lambda x: f"https://www.youtube.com/channel/{x}")
                                # 日付を読みやすい形式に変換 (タイムゾーン情報を削除して表示)
                                new_channels_df['開設日'] = new_channels_df['published_at'].dt.tz_localize(None).dt.strftime('%Y-%m-%d')
                                new_channels_df['登録者数'] = new_channels_df.apply(
                                    lambda row: f"{row['subscriber_count']:,}" if not row['hidden_subscriber_count'] else "非公開",
                                    axis=1
                                )
                                new_channels_df['総再生回数'] = new_channels_df['view_count'].apply(lambda x: f"{x:,}")
                                new_channels_df['動画数'] = new_channels_df['video_count'].apply(lambda x: f"{x:,}")
                                new_channels_df['平均再生数/本'] = new_channels_df['avg_views_per_video'].apply(lambda x: f"{int(x):,}")
                                new_channels_df['再生/登録比'] = new_channels_df['views_per_sub_ratio'].apply(lambda x: f"{x:.1f}x")
                                
                                # 表示する列を選択し、並び替え (登録者数で降順ソート)
                                display_df = new_channels_df.sort_values(by='subscriber_count', ascending=False)

                                display_columns = {
                                    'channel_name': 'チャンネル名',
                                    'growth_factors': '成長要因', # 追加
                                    '開設日': '開設日',
                                    'country': '国', # 追加
                                    '登録者数': '登録者数',
                                    '総再生回数': '総再生回数',
                                    '動画数': '動画数',
                                    '平均再生数/本': '平均再生/本', # 追加
                                    '再生/登録比': '再生/登録比',   # 追加
                                    'channel_link': 'チャンネルリンク'
                                }
                                st.dataframe(display_df[list(display_columns.keys())].rename(columns=display_columns),
                                             hide_index=True,
                                             column_config={
                                                "チャンネルリンク": st.column_config.LinkColumn("リンク", display_text="開く"),
                                                "成長要因": st.column_config.ListColumn("成長要因") # 簡易的な表示
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
                                if strict_country_filter:
                                    st.info(f"条件に合致する新規チャンネルは見つかりませんでした（国のフィルタリングが影響している可能性があります）。")
                                else:
                                    st.info(f"直近{NEW_CHANNEL_DAYS_THRESHOLD}日以内に開設されたチャンネルは、今回の検索範囲（人気動画上位{MAX_TOTAL_VIDEO_RESULTS}件由来）には見つかりませんでした。")
        else:  # チャンネル内再生ランキングモード
            if channel_id and st.button("🏆 ランキング表示", key="show_ranking"):
                with st.spinner("動画情報を取得中..."):
                    videos = get_channel_videos(youtube_service, channel_id, ranking_limit)
                    
                    if videos:
                        # 1日平均再生数で算出・ソート
                        videos_df = pd.DataFrame(videos)
                        videos_df['published_at'] = pd.to_datetime(videos_df['published_at'])
                        
                        # 経過日数を計算 (公開日が今日の場合は1日とする)
                        # todayはループの外で timezone.utc で定義済み
                        videos_df['days_elapsed'] = (today - videos_df['published_at']).dt.days
                        videos_df['days_elapsed'] = videos_df['days_elapsed'].apply(lambda x: x if x > 0 else 1)
                        
                        # 1日あたりの平均再生数を計算
                        videos_df['daily_views'] = videos_df['view_count'] / videos_df['days_elapsed']
                        
                        # 平均再生数でソート
                        videos_df = videos_df.sort_values('daily_views', ascending=False)
                        
                        # 表示用にデータを整形
                        videos_df['公開日'] = videos_df['published_at'].dt.strftime('%Y-%m-%d')
                        videos_df['1日平均再生数'] = videos_df['daily_views'].apply(lambda x: f"{int(x):,}")
                        videos_df['再生回数'] = videos_df['view_count'].apply(lambda x: f"{x:,}")
                        videos_df['高評価数'] = videos_df['like_count'].apply(lambda x: f"{x:,}")
                        videos_df['コメント数'] = videos_df['comment_count'].apply(lambda x: f"{x:,}")
                        videos_df['動画リンク'] = videos_df['video_id'].apply(lambda x: f"https://www.youtube.com/watch?v={x}")
                        
                        # データフレームを表示
                        display_cols = ['title', '公開日', '1日平均再生数', '再生回数', '高評価数', 'コメント数', '動画リンク']
                        display_cols_map = {'title': 'タイトル'}
                        
                        st.dataframe(
                            videos_df[display_cols].rename(columns=display_cols_map),
                            hide_index=True,
                            column_config={
                                "動画リンク": st.column_config.LinkColumn("リンク", display_text="開く")
                            }
                        )
                        
                        # CSVダウンロードボタン
                        @st.cache_data
                        def convert_df_to_csv(df_to_convert):
                            return df_to_convert.to_csv(index=False).encode('utf-8-sig')
                            
                        csv = convert_df_to_csv(videos_df[display_cols].rename(columns=display_cols_map))
                        
                        st.download_button(
                            label="📥 結果をCSVでダウンロード",
                            data=csv,
                            file_name=f"youtube_channel_ranking_{channel_id}_{today.strftime('%Y%m%d')}.csv",
                            mime='text/csv',
                        )
                    else:
                        st.warning("動画情報の取得に失敗しました。チャンネルIDを確認してください。")
            else:
                st.info("チャンネルIDを入力し、「ランキング表示」ボタンを押してください。")

else:
    st.info("サイドバーからYouTube Data API v3 キーを入力してください。")

st.markdown("---")
st.caption("注意: APIのクォータ制限、チャンネル設定（登録者非公開など）、検索範囲の限界により、すべての情報を網羅できるわけではありません。")
