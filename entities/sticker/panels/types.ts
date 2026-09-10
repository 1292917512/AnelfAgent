export interface StickerItem {
  id: string;
  description: string;
  tags: string[];
  emotion: string;
  file_path: string;
  content_hash: string;
  phash: string;
  source: string;
  use_count: number;
  created_ns: number;
  updated_ns: number;
  has_embedding: boolean;
}

export interface StickerListResult {
  items: StickerItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface StickerEmbeddingHealth {
  model: string;
  model_dims: number | null;
  vec_dims: Record<string, number | null>;
  stored_dims: Record<string, Record<string, number>>;
  missing: Record<string, number>;
  mismatched: number;
}

export interface StickerStats {
  stickers: number;
  total_uses: number;
  indexed_images: number;
  described_images: number;
  vec_available: boolean;
  embedding: StickerEmbeddingHealth;
}

export interface IndexedImage {
  path: string;
  description: string;
  content_hash: string;
  phash: string;
  source: string;
  ts_ns: number;
  has_embedding: boolean;
}

export interface IndexedImageListResult {
  items: IndexedImage[];
  total: number;
  page: number;
  page_size: number;
}
