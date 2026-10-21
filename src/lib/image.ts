export interface LoadedImage {
  dataUrl: string;
  width: number;
  height: number;
  name: string;
  type: string;
}

export const MAX_IMAGE_BYTES = 12 * 1024 * 1024;
export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/avif', 'image/gif', 'image/bmp'];

export function isSupportedImage(file: File): boolean {
  if (file.type.startsWith('image/')) return true;
  return /\.(png|jpe?g|webp|avif|gif|bmp)$/i.test(file.name);
}

export function readImage(file: File): Promise<LoadedImage> {
  return new Promise((resolve, reject) => {
    if (!isSupportedImage(file)) {
      reject(new Error(`«${file.name}» — не похоже на изображение. Нужен PNG, JPEG, WebP, AVIF, GIF или BMP.`));
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      reject(new Error(`Файл больше ${Math.round(MAX_IMAGE_BYTES / 1024 / 1024)} МБ. Сожмите картинку или уменьшите разрешение.`));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Не удалось прочитать файл.'));
    reader.onload = () => {
      const dataUrl = String(reader.result ?? '');
      if (!dataUrl.startsWith('data:image/')) {
        reject(new Error('Файл не является изображением.'));
        return;
      }
      const img = new Image();
      img.onerror = () => reject(new Error('Картинка повреждена или формат не поддерживается браузером.'));
      img.onload = () =>
        resolve({
          dataUrl,
          width: img.naturalWidth,
          height: img.naturalHeight,
          name: file.name.replace(/\.[^.]+$/, '') || 'Мир',
          type: file.type || 'image/png',
        });
      img.src = dataUrl;
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Если картинка слишком большая для localStorage — пересобираем её в JPEG
 * с ограничением по длинной стороне.
 */
export function shrinkImage(image: LoadedImage, maxSide = 4096, quality = 0.85): Promise<LoadedImage> {
  return new Promise((resolve) => {
    const longest = Math.max(image.width, image.height);
    if (longest <= maxSide) {
      resolve(image);
      return;
    }
    const scale = maxSide / longest;
    const w = Math.round(image.width * scale);
    const h = Math.round(image.height * scale);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        resolve(image);
        return;
      }
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, 0, 0, w, h);
      resolve({
        dataUrl: canvas.toDataURL('image/jpeg', quality),
        width: w,
        height: h,
        name: image.name,
        type: 'image/jpeg',
      });
    };
    img.onerror = () => resolve(image);
    img.src = image.dataUrl;
  });
}
