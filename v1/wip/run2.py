from rapidocr import RapidOCR

engine = RapidOCR()

img_url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/resources/test_files/ch_en_num.jpg"


result = engine(img_url)
print(result)

img = 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.jpg'
result.vis(img)