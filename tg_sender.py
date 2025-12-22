import requests
import time
import re
import json

from loguru import logger

from models import Item


class SendAdToTg:
    def __init__(self, bot_token: str, chat_id: list, max_retries: int = 5, retry_delay: int = 5):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # Используйте эндпоинт sendMediaGroup
        self.media_group_url = f"https://api.telegram.org/bot{self.bot_token}/sendMediaGroup"

    @staticmethod
    def escape_markdown(text: str) -> str:
        """Экранирует спецсимволы MarkdownV2, кроме """
        if not text:
            return ""
        text = str(text).replace("\xa0", " ")
        return re.sub(r'([_\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

    def __send_to_tg(self, chat_id: str | int, ad: Item = None, msg: str = None):
        message = self.format_ad(ad)
        images = self.get_images(ad=ad)  # Предполагается, что get_images возвращает список URL изображений

        if not images:
            logger.warning(f"No images found {ad}")
            images = ["https://i.ibb.co/rG7MgdfF/1887013-middle.png"]

        media_group = [
            {"type": "photo", "media": image} for image in images[:10]
        ]
        
        # Добавьте подпись к первому изображению
        media_group[0]["caption"] = message[:1000]
        media_group[0]["parse_mode"] = "MarkdownV2"

        for attempt in range(1, self.max_retries + 1):
            try:
                payload = {
                    "chat_id": chat_id,
                    "media": json.dumps(media_group),
                }
                logger.info(payload)

                response = requests.post(self.media_group_url, json=payload)
                if response.status_code == 400:
                    logger.warning(
                        f"Не удалось отправить сообщения. Проверьте правильность введенных данных\n"
                        f"{response.text}\n"
                        f"{payload}")
                    break

                response.raise_for_status()
                logger.debug(f"Сообщение успешно отправлено (попытка {attempt})")
                break
            except requests.RequestException as e:
                logger.debug(f"Ошибка при отправке (попытка {attempt}): {e}")
                logger.debug(message)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    logger.debug("Не удалось отправить сообщение после всех попыток.")

    def get_images(self, ad: Item):
        # Здесь должна быть реализация, возвращающая список URL изображений

        def get_largest_image_url(img):
            best_key = max(
                img.root.keys(),
                key=lambda k: int(k.split("x")[0]) * int(k.split("x")[1])
            )
            return str(img.root[best_key])
        temp = []

        images_urls = [get_largest_image_url(img) for img in ad.images]
        if images_urls:
            temp = images_urls

        return temp

    def send_to_tg(self, ad: Item = None, msg: str = None):
        for chat_id in self.chat_id:
            self.__send_to_tg(chat_id=chat_id, ad=ad, msg=msg)

    @staticmethod
    def get_first_image(ad: Item):
        def get_largest_image_url(img):
            best_key = max(
                img.root.keys(),
                key=lambda k: int(k.split("x")[0]) * int(k.split("x")[1])
            )
            return str(img.root[best_key])

        images_urls = [get_largest_image_url(img) for img in ad.images]
        if images_urls:
            return images_urls[0]

    @staticmethod
    def format_ad(ad: Item) -> str:
        def esc(text: str) -> str:
            if not text:
                return ""
            s = str(text).replace("\xa0", " ")
            return re.sub(r'([_\[\]()~`>#+\-=|{}.!])', r'\\\1', s)

        py_ad = ad.model_dump(mode="python")

        price = py_ad.get("priceDetailed", {}).get("value", "") if py_ad.get("priceDetailed") else ""
        title = esc(getattr(ad, "title", ""))
        short_url = f"https://avito.ru/{getattr(ad, 'id', '')}"
        seller = esc(str(getattr(ad, "sellerId", ""))) if getattr(ad, "sellerId", None) else ""
        description = py_ad.get("description", "")
        address = py_ad.get("geo", {}).get("formattedAddress", "")

        parts = []

        if title:
            parts.append(f"[{title}]({short_url})")

        if description:
            parts.append(f"{esc(description.strip())}\n")

        if price:
            price_part = f"Цена: *{price}*"
            parts.append(price_part)

        if seller:
            parts.append(f"Продавец: {seller.strip()}")

        if address:
            parts.append(f"{esc(address)}")

        message = "\n".join(parts)
        return message
