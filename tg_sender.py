import telebot
from telebot.types import InputMediaPhoto
import logging
import re
import time
import json
import requests
from io import BytesIO  # Для работы с байтами изображений в памяти

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Заглушки для AvitoConfig и Item, чтобы код был запускаемым
class AvitoConfig:
    def __init__(self, http_proxy: str = None):
        self.http_proxy = http_proxy


class Item:
    def __init__(self, id: str, title: str, description: str, images: list, price_detailed: dict = None,
                 seller_id: str = None, geo: dict = None):
        self.id = id
        self.title = title
        self.description = description
        self.images = images  # Список объектов, представляющих изображения
        self.priceDetailed = price_detailed
        self.sellerId = seller_id
        self.geo = geo

    def model_dump(self, mode="python"):
        # Простая имитация pydantic model_dump
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "images": self.images,
            "priceDetailed": self.priceDetailed,
            "sellerId": self.sellerId,
            "geo": self.geo,
        }


# Класс для "корня" изображения, чтобы имитировать img.root из первого примера
class ImageDataRoot:
    def __init__(self, data: dict):
        self.root = data  # data будет словарем вида {"640x480": "url", ...}

    def model_dump(self, mode="json"):
        # Если ваш Item.images содержит настоящие объекты, а не dict, то этот метод должен быть в них
        return self.root


class SendAdToTg:
    def __init__(self, bot_token: str, chat_id: list, config: AvitoConfig, max_retries: int = 5, retry_delay: int = 5):
        self.bot_token = bot_token
        self.chat_ids = chat_id
        self.config: AvitoConfig = config
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        telebot.apihelper.proxy = {"http": self.config.http_proxy, "https": self.config.http_proxy}

        self.bot = telebot.TeleBot(token=self.bot_token, threaded=False)

    @staticmethod
    def escape_markdown(text: str) -> str:
        """Экранирует спецсимволы MarkdownV2."""
        return telebot.formatting.escape_markdown(text)

    def _download_image(self, url: str) -> BytesIO | None:
        """Скачивает изображение по URL и возвращает его байты в BytesIO."""
        response = requests.get(url, timeout=20)
        response.raise_for_status()  # Вызывает исключение для плохих статусов (4xx или 5xx)
        image_data = BytesIO(response.content)
        return image_data

    def __send_to_tg(self, chat_id: str | int, ad: Item = None):
        message = self.format_ad(ad)
        image_urls = self.get_images(ad=ad)  # Получаем список URL изображений

        if not image_urls:
            logger.warning(f"No images found for ad: {ad.id if ad else 'N/A'}. Using fallback image.")
            image_urls = ["https://i.ibb.co/rG7MgdfF/1887013-middle.png"]  # Запасное изображение

        media_group = []
        downloaded_images_count = 0

        for i, url in enumerate(image_urls[:10]):
            image_bytes_io = self._download_image(url)
            if image_bytes_io:
                downloaded_images_count += 1
                if i == 0:
                    # Добавляем подпись к первому изображению
                    # parse_mode="MarkdownV2" должен быть здесь, чтобы экранирование работало
                    media_group.append(
                        InputMediaPhoto(media=image_bytes_io, caption=message[:1000], parse_mode="HTML"))
                else:
                    media_group.append(InputMediaPhoto(media=image_bytes_io))
            else:
                logger.warning(f"Пропущено изображение из-за ошибки загрузки: {url}")

        if not media_group and downloaded_images_count == 0:
            logger.error(
                f"Не удалось загрузить ни одного изображения для объявления {ad.id if ad else 'N/A'}. Отмена отправки.")
            return

        for attempt in range(1, self.max_retries + 1):
            try:
                self.bot.send_media_group(chat_id=chat_id, media=media_group)
                logger.debug(f"Сообщение успешно отправлено (попытка {attempt}) в чат {chat_id}")
                break
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 400:
                    logger.warning(
                        f"Не удалось отправить сообщения в чат {chat_id}. Проверьте правильность введенных данных\n"
                        f"Ошибка: {e.result_json}\n"
                        f"Изначальное сообщение (часть): {message[:100]}"
                    )
                    break
                logger.debug(f"Ошибка при отправке (попытка {attempt}): {e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(f"Не удалось отправить сообщение после всех попыток в чат {chat_id}.")
            except requests.exceptions.RequestException as e:
                logger.debug(f"Ошибка HTTP запроса (попытка {attempt}): {e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(
                        f"Не удалось отправить сообщение из-за сетевой ошибки после всех попыток в чат {chat_id}.")
            except Exception as e:
                logger.error(f"Неизвестная ошибка при отправке сообщения (попытка {attempt}): {e}", exc_info=True)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(
                        f"Не удалось отправить сообщение из-за неизвестной ошибки после всех попыток в чат {chat_id}.")

    def get_images(self, ad: Item) -> list[str]:
        def get_largest_image_url(img_data_root: ImageDataRoot) -> str | None:
            img_obj = img_data_root.root
            best_key = max(
                img_obj.keys(),
                key=lambda k: int(k.split("x")[0]) * int(k.split("x")[1]),
                default=None
            )
            return img_obj[best_key] if best_key else None

        if not ad or not ad.images:
            return []

        images_urls = []
        for img in ad.images:
            # Предполагаем, что img является экземпляром ImageDataRoot или похожим объектом с атрибутом .root
            url = get_largest_image_url(img)
            if url:
                images_urls.append(url)
        return images_urls

    def send_to_tg(self, ad: Item = None):
        if not ad:
            logger.warning("Attempted to send an empty ad.")
            return

        for chat_id in self.chat_ids:
            self.__send_to_tg(chat_id=chat_id, ad=ad)

    @staticmethod
    def get_first_image(ad: Item) -> str | None:
        def get_largest_image_url(img_data_root: ImageDataRoot) -> str | None:
            img_obj = img_data_root.root
            best_key = max(
                img_obj.keys(),
                key=lambda k: int(k.split("x")[0]) * int(k.split("x")[1]),
                default=None
            )
            return img_obj[best_key] if best_key else None

        if not ad or not ad.images:
            return None

        first_image_obj = ad.images[0]
        return get_largest_image_url(first_image_obj)

    @staticmethod
    def format_ad(ad: Item) -> str:
        # Используем метод escape_markdown класса для единообразия
        esc = SendAdToTg.escape_markdown

        py_ad = ad.model_dump(mode="python")

        price = py_ad.get("priceDetailed", {}).get("value", "") if py_ad.get("priceDetailed") else ""
        title = esc(getattr(ad, "title", ""))
        short_url = f"https://avito.ru/{getattr(ad, 'id', '')}"  # URL не экранируется, но ID в нем может содержать спецсимволы.
        # Для URL лучше не использовать общий escap_markdown
        seller = esc(str(getattr(ad, "sellerId", ""))) if getattr(ad, "sellerId", None) else ""
        description = py_ad.get("description", "")
        address = py_ad.get("geo", {}).get("formattedAddress", "")

        parts = []

        if title:
            parts.append(f'<a href="{short_url}">{title}</a>')  # Вернул ссылку в заголовок

        if description:
            parts.append(f"{description.strip()}\n")

        if price:
            price_part = f"Цена: <b>{str(price)}</b> Р"  # Экранируем '*' телеграма
            parts.append(price_part)

        if seller:
            parts.append(f"Продавец: {seller.strip()}")

        if address:
            parts.append(f"Адрес: {address}")

        # Убрал повтор ссылки, если она уже есть в заголовке
        message = "\n".join(parts)
        return message
