import discord
import os
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from pdf2image import convert_from_bytes
import requests
import io
import asyncio
import json
import urllib.parse
import traceback

# 0. 環境変数 (Secrets) から情報を取得
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
GOOGLE_SHEETS_CREDENTIALS_JSON_STR = os.environ.get(
    'GOOGLE_SHEETS_CREDENTIALS')
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

# ボットの設定値
TARGET_TRIGGER = "KCG-"  # 条件1: この文字列で始まる

# 書き込み関連の設定
WRITE_TARGET_SHEET_NAME = "表示1"
WRITE_TARGET_CELL_C14_LABEL = "C14"  # ラベル書き込み用セル
WRITE_TARGET_CELL = "C15"  # メインのメッセージ書き込み用セル (元々のC15)

# 画像生成関連の設定
IMAGE_CAPTURE_SHEET_NAME = "表示1"
IMAGE_CAPTURE_RANGE = "A1:H12"
DELAY_SECONDS = 1

# 1. Discordボットの設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# 2. Googleスプレッドシートへの接続設定
gc = None
spreadsheet = None
global_creds = None

if GOOGLE_SHEETS_CREDENTIALS_JSON_STR and SPREADSHEET_ID:
    try:
        creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON_STR)
        global_creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"
            ])
        gc = gspread.authorize(global_creds)
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        print("Google Sheetsに正常に接続しました。")
    except json.JSONDecodeError:
        print("エラー: GOOGLE_SHEETS_CREDENTIALSのJSON形式が正しくありません。")
        global_creds = None
    except Exception as e:
        print(f"Google Sheetsへの接続中にエラーが発生しました: {e}")
        global_creds = None
else:
    print("エラー: Google Sheets接続に必要な情報 (認証情報またはスプレッドシートID) がSecretsに設定されていません。")


# 3. PDFエクスポートとpdf2imageで画像を生成する関数
def create_spreadsheet_image_from_pdf(gs_spreadsheet_id, sheet_gid_to_capture,
                                      sheet_range_to_capture, credentials_obj):
    try:
        if not credentials_obj:
            raise ValueError("認証情報オブジェクトが提供されていません。")

        base_export_url = f"https://docs.google.com/spreadsheets/d/{gs_spreadsheet_id}/export"

        export_params = {
            "format": "pdf",
            "gid": str(sheet_gid_to_capture),
            "range": sheet_range_to_capture,
            "portrait": "false",
            "scale": "4",
            "gridlines": "true",
            "printtitle": "false",
            "sheetnames": "false",
            "pagenumbers": "false",
            "attachment": "false",
            "top_margin": "0.25",
            "bottom_margin": "0.25",
            "left_margin": "0.25",
            "right_margin": "0.25"
        }

        pdf_export_url = f"{base_export_url}?{urllib.parse.urlencode(export_params)}"
        print(f"PDFエクスポートURL: {pdf_export_url}")

        if not credentials_obj.valid:
            print("アクセストークンが無効または期限切れのため、リフレッシュします。")
            credentials_obj.refresh(GoogleAuthRequest())

        access_token = credentials_obj.token
        if not access_token:
            raise ValueError("アクセストークンの取得に失敗しました。")

        headers = {'Authorization': 'Bearer ' + access_token}

        response = requests.get(pdf_export_url, headers=headers, timeout=30)
        response.raise_for_status()

        pdf_bytes = response.content

        if not pdf_bytes:
            raise ValueError("ダウンロードされたPDFデータが空です。")

        images = convert_from_bytes(pdf_bytes,
                                    dpi=200,
                                    first_page=1,
                                    last_page=1)

        if not images:
            raise ValueError("PDFから画像を変換できませんでした。")

        img_byte_arr = io.BytesIO()
        images[0].save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        print("PDFから画像の生成に成功しました。")
        return img_byte_arr

    except requests.exceptions.HTTPError as http_err:
        print(f"PDFダウンロード時のHTTPエラー: {http_err}")
        error_response_text = "N/A"
        if http_err.response is not None:
            error_response_text = http_err.response.text[:500]
        print(f"レスポンス内容 (冒頭500文字): {error_response_text}")
        return None
    except Exception as e:
        print(f"画像生成(PDF経由)中にエラーが発生しました: {e}")
        traceback.print_exc()
        return None


@client.event
async def on_ready():
    print(f'{client.user} としてログインしました')
    if spreadsheet is None or global_creds is None:
        print(f"注意: Google Sheetsの接続または認証情報が初期化されていません。")
        print(f"SPREADSHEET_ID: {SPREADSHEET_ID}")
        print(
            f"GOOGLE_SHEETS_CREDENTIALS設定状況: {'設定済み' if GOOGLE_SHEETS_CREDENTIALS_JSON_STR else '未設定'}"
        )
        print(f"global_creds初期化状況: {'成功' if global_creds else '失敗または未設定'}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    triggered_by_kcg = message.content.startswith(TARGET_TRIGGER)
    triggered_by_slashes = message.content.count('/') == 59

    if triggered_by_kcg or triggered_by_slashes:
        if spreadsheet is None or global_creds is None:
            await message.channel.send(
                "エラー: Google Sheetsの接続または認証情報が初期化されていません。設定を確認してください。")
            return

        received_text = message.content

        try:
            # 1. 書き込み用シートを選択
            worksheet_for_write = None
            try:
                worksheet_for_write = spreadsheet.worksheet(
                    WRITE_TARGET_SHEET_NAME)
            except gspread.exceptions.WorksheetNotFound:
                await message.channel.send(
                    f"エラー: 書き込み用シート '{WRITE_TARGET_SHEET_NAME}' が見つかりませんでした。")
                return
            except Exception as e:
                await message.channel.send(
                    f"書き込み用シート '{WRITE_TARGET_SHEET_NAME}' を開く際に予期せぬエラーが発生しました: {e}"
                )
                print(f"書き込みシートオープンエラー詳細: {e}")
                traceback.print_exc()
                return

            # 2. 条件に応じてC14セルに特定の文字列を書き込む
            label_for_c14 = None
            if triggered_by_kcg:
                label_for_c14 = "デッキコード"
            elif triggered_by_slashes:  # KCG-トリガーが優先されるようにelifを使用
                label_for_c14 = "デッキリスト"

            if label_for_c14:
                worksheet_for_write.update_acell(WRITE_TARGET_CELL_C14_LABEL,
                                                 label_for_c14)

            # 3. C15セル (WRITE_TARGET_CELL) に受信したメッセージ全体を書き込む
            worksheet_for_write.update_acell(WRITE_TARGET_CELL, received_text)

            # 4. X秒待機
            await asyncio.sleep(DELAY_SECONDS)

            # 5. 画像生成用シートのGIDを取得
            worksheet_for_image = None
            sheet_gid_for_image = None
            try:
                worksheet_for_image = spreadsheet.worksheet(
                    IMAGE_CAPTURE_SHEET_NAME)
                sheet_gid_for_image = worksheet_for_image.id
            except gspread.exceptions.WorksheetNotFound:
                await message.channel.send(
                    f"エラー: 画像生成用シート '{IMAGE_CAPTURE_SHEET_NAME}' が見つかりませんでした。")
                return
            except Exception as e:
                await message.channel.send(
                    f"画像生成用シート '{IMAGE_CAPTURE_SHEET_NAME}' の情報を取得する際に予期せぬエラーが発生しました: {e}"
                )
                print(f"画像シート情報取得エラー詳細: {e}")
                traceback.print_exc()
                return

            if SPREADSHEET_ID is None or sheet_gid_for_image is None:
                await message.channel.send(
                    "エラー: 画像生成に必要なスプレッドシートIDまたはシートGIDが取得できません。")
                return

            image_bytes = create_spreadsheet_image_from_pdf(
                SPREADSHEET_ID, sheet_gid_for_image, IMAGE_CAPTURE_RANGE,
                global_creds)

            if image_bytes:
                discord_file = discord.File(fp=image_bytes,
                                            filename="spreadsheet_capture.png")
                await message.channel.send(file=discord_file)
            else:
                await message.channel.send(f"画像の生成に失敗しました。コンソールログで詳細を確認してください。"
                                           )

        except gspread.exceptions.APIError as e_gspread:
            error_details = e_gspread.args[0] if e_gspread.args else {}
            if isinstance(error_details, dict):
                error_code = error_details.get('code')
                error_msg = error_details.get('message', str(e_gspread))
                if error_code == 403:
                    await message.channel.send(
                        f"スプレッドシートへの書き込み/読み取り権限がありません。シートの共有設定やAPIの有効化を確認してください。\nエラー: {error_msg}"
                    )
                else:
                    await message.channel.send(
                        f"スプレッドシート操作中にAPIエラーが発生しました (コード: {error_code}): {error_msg}"
                    )
            else:
                await message.channel.send(
                    f"スプレッドシート操作中にAPIエラーが発生しました: {e_gspread}")
            print(f"Google API Error: {e_gspread}")
            traceback.print_exc()

        except Exception as e:
            await message.channel.send(
                f"処理中に予期せぬエラーが発生しました: {type(e).__name__} - {e}")
            print(f"予期せぬエラー詳細 (on_message): {e}")
            traceback.print_exc()


# keep_alive.py を使って常時起動する場合
from keep_alive import keep_alive

keep_alive()

if DISCORD_TOKEN:
    try:
        client.run(DISCORD_TOKEN)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            print("Discord APIレートリミットに達しました。しばらく待ってから再起動してください。")
        elif e.status == 401 or e.status == 403:
            print("エラー: 無効なDiscordトークンです。ReplitのSecretsを確認してください。")
        else:
            print(f"Discord接続エラー: {e}")
        traceback.print_exc()
    except Exception as e:
        print(f"ボット起動時に予期せぬエラー: {e}")
        traceback.print_exc()
else:
    print("エラー: DISCORD_TOKENが設定されていません。ReplitのSecretsを確認してください。")
