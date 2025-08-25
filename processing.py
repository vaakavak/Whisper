import os
import torch
import whisper
from transformers import T5ForConditionalGeneration, T5Tokenizer
from celery import shared_task
import re
import logging

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Model Caching ---
# Use a dictionary to cache loaded models to avoid reloading them on every task.
# This is a simple in-memory cache, suitable for a single worker process.
# For multi-worker setups, a more sophisticated caching mechanism might be needed.
model_cache = {}

def get_whisper_model(model_name="small"):
    """Loads a Whisper model from cache or from disk."""
    if model_name not in model_cache:
        logger.info(f"Loading Whisper model: {model_name}")
        model_cache[model_name] = whisper.load_model(model_name)
        logger.info(f"Whisper model '{model_name}' loaded.")
    return model_cache[model_name]

def get_summarizer_model():
    """Loads the FRED-T5 summarizer model and tokenizer from cache or Hugging Face."""
    model_key = "summarizer"
    tokenizer_key = "summarizer_tokenizer"
    if model_key not in model_cache:
        logger.info("Loading FRED-T5 summarizer model...")
        model_name = "RussianNLP/FRED-T5-Summarizer"
        model_cache[model_key] = T5ForConditionalGeneration.from_pretrained(model_name)
        model_cache[tokenizer_key] = T5Tokenizer.from_pretrained(model_name)
        logger.info("FRED-T5 summarizer model loaded.")
    return model_cache[model_key], model_cache[tokenizer_key]

# --- Helper Functions ---

def format_timestamp(seconds):
    """Converts seconds to HH:MM:SS,ms format."""
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)

    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000

    minutes = milliseconds // 60_000
    milliseconds %= 60_000

    seconds = milliseconds // 1_000
    milliseconds %= 1_000

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def generate_timestamped_text(segments):
    """Generates a VTT-like string from Whisper segments."""
    text = ""
    for segment in segments:
        start_time = format_timestamp(segment['start'])
        end_time = format_timestamp(segment['end'])
        text += f"{start_time} --> {end_time}\n{segment['text'].strip()}\n\n"
    return text

def summarize_text(text):
    """Summarizes text using the FRED-T5 model."""
    # The model expects a prefix "summarize: "
    text_to_summarize = f"summarize: {text}"

    model, tokenizer = get_summarizer_model()

    input_ids = tokenizer.encode(text_to_summarize, return_tensors="pt", max_length=1024, truncation=True)

    # Generate summary
    summary_ids = model.generate(
        input_ids,
        max_length=250,
        min_length=50,
        num_beams=4,
        length_penalty=2.0,
        early_stopping=True
    )

    summary = tokenizer.decode(summary_ids[0], skip_special_tokens=True)
    return summary

# --- Celery Task Definition ---

@shared_task(bind=True)
def process_audio_task(self, filepath, model_name, start_words_str, stop_words_str):
    """
    The main Celery task for audio processing.
    'bind=True' gives us access to 'self' for status updates.
    """
    try:
        # 1. Update Status: Loading models
        self.update_state(state='PROGRESS', meta={'progress': 5, 'status': 'Загрузка моделей...'})

        whisper_model = get_whisper_model(model_name)
        get_summarizer_model() # Load summarizer model into cache

        # Parse start/stop words
        start_words = [word.strip().lower() for word in start_words_str.split(',') if word.strip()]
        stop_words = [word.strip().lower() for word in stop_words_str.split(',') if word.strip()]

        # 2. Update Status: Transcribing
        self.update_state(state='PROGRESS', meta={'progress': 20, 'status': 'Транскрибация аудио...'})

        logger.info(f"Starting transcription for {filepath} with model {model_name}")
        # We use fp16=False if no CUDA is available
        options = {"fp16": torch.cuda.is_available()}
        result = whisper_model.transcribe(filepath, **options)
        logger.info("Transcription finished.")

        # 3. Process transcription results (handle start words)
        full_text = ""
        timestamped_text = ""
        final_segments = []

        halted = False
        for segment in result['segments']:
            segment_text_lower = segment['text'].lower()
            # Check for start words in the current segment
            for word in start_words:
                if word in segment_text_lower:
                    # If a start word is found, truncate the text and stop
                    halt_index = segment_text_lower.find(word)
                    segment['text'] = segment['text'][:halt_index]
                    final_segments.append(segment)
                    halted = True
                    break
            if halted:
                break
            final_segments.append(segment)

        full_text = " ".join([seg['text'].strip() for seg in final_segments])
        timestamped_text = generate_timestamped_text(final_segments)

        # 4. Filter stop words from the final text
        if stop_words:
            # Use regex to remove whole words, case-insensitively
            stop_word_pattern = r'\b(' + '|'.join(re.escape(word) for word in stop_words) + r')\b'
            full_text = re.sub(stop_word_pattern, '', full_text, flags=re.IGNORECASE)
            full_text = re.sub(r'\s+', ' ', full_text).strip() # Clean up extra spaces

        # 5. Update Status: Summarizing
        self.update_state(state='PROGRESS', meta={'progress': 80, 'status': 'Суммаризация текста...'})

        summary = ""
        if full_text:
            logger.info("Starting summarization...")
            summary = summarize_text(full_text)
            logger.info("Summarization finished.")
        else:
            summary = "Текст для суммаризации отсутствует."

        # 6. Clean up the uploaded file
        os.remove(filepath)
        logger.info(f"Removed temporary file: {filepath}")

        # 7. Return final results
        return {
            'transcription': full_text,
            'timestamps': timestamped_text,
            'summary': summary
        }

    except Exception as e:
        logger.error(f"Error in task {self.request.id}: {e}", exc_info=True)
        # Clean up file on error as well
        if os.path.exists(filepath):
            os.remove(filepath)
        # Propagate the exception to be stored in the task result
        raise e
