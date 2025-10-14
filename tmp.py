import google.generativeai as genai
import inspect
print('has images', hasattr(genai, 'images'))
if hasattr(genai, 'images'):
    attrs = dir(genai.images)
    print('image attrs', [n for n in attrs if 'generate' in n.lower()])
print('ImageGenerationModel?', hasattr(genai, 'ImageGenerationModel'))
if hasattr(genai, 'ImageGenerationModel'):
    model_cls = genai.ImageGenerationModel
    print('ImageGenerationModel methods', [n for n in dir(model_cls) if 'generate' in n.lower()])
