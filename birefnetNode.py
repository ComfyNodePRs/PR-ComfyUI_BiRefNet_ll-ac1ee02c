import os
import safetensors.torch
import torch
from torchvision import transforms
from torch.hub import download_url_to_file
import comfy
from comfy import model_management
import folder_paths
from birefnet.config import Config
from birefnet.models.birefnet import BiRefNet
from birefnet.utils import check_state_dict
from .util import tensor_to_pil, apply_mask_to_image

config = Config()

deviceType = model_management.get_torch_device().type

models_dir_key = "birefnet"
models_dir_default = os.path.join(folder_paths.models_dir, "BiRefNet")
if models_dir_key not in folder_paths.folder_names_and_paths:
    folder_paths.folder_names_and_paths[models_dir_key] = (
        [os.path.join(folder_paths.models_dir, "BiRefNet")], folder_paths.supported_pt_extensions)
else:
    if not os.path.exists(models_dir_default):
        os.makedirs(models_dir_default, exist_ok=True)
    folder_paths.add_model_folder_path(models_dir_key, models_dir_default)

models_path_default = folder_paths.get_folder_paths(models_dir_key)[0]

usage_to_weights_file = {
    'General': 'BiRefNet',
    'General-Lite': 'BiRefNet_T',
    'Portrait': 'BiRefNet-portrait',
    'DIS': 'BiRefNet-DIS5K',
    'HRSOD': 'BiRefNet-HRSOD',
    'COD': 'BiRefNet-COD',
    'DIS-TR_TEs': 'BiRefNet-DIS5K-TR_TEs'
}

modelNameList = ['General', 'General-Lite', 'Portrait', 'DIS', 'HRSOD', 'COD', 'DIS-TR_TEs']


def get_model_path(model_name):
    return os.path.join(models_path_default, f"{model_name}.safetensors")


def download_models(model_root, model_urls):
    if not os.path.exists(model_root):
        os.makedirs(model_root, exist_ok=True)

    for local_file, url in model_urls:
        local_path = os.path.join(model_root, local_file)
        if not os.path.exists(local_path):
            local_path = os.path.abspath(os.path.join(model_root, local_file))
            download_url_to_file(url, dst=local_path)


def download_birefnet_model(model_name):
    """
    Downloading model from huggingface.
    """
    model_root = os.path.join(models_path_default)
    model_urls = (
        (f"{model_name}.safetensors",
         f"https://huggingface.co/ZhengPeng7/{usage_to_weights_file[model_name]}/resolve/main/model.safetensors"),
    )
    download_models(model_root, model_urls)


proc_img = transforms.Compose(
    [
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)


class AutoDownloadBiRefNetModel:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_name": (modelNameList,),
                "device": (["AUTO", "CPU"],)
            }
        }

    RETURN_TYPES = ("BiRefNetMODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_model"
    CATEGORY = "image/BiRefNet"
    DESCRIPTION = "Auto download BiRefNet model from huggingface to models/BiRefNet/{model_name}.safetensors"

    def load_model(self, model_name, device):
        bb_index = 3 if model_name == "General-Lite" else 6
        biRefNet_model = BiRefNet(bb_pretrained=False, bb_index=bb_index)
        model_file_name = f'{model_name}.safetensors'
        model_full_path = folder_paths.get_full_path(models_dir_key, model_file_name)
        if model_full_path is None:
            download_birefnet_model(model_name)
            model_full_path = folder_paths.get_full_path(models_dir_key, model_file_name)
        if device == "AUTO":
            device_type = deviceType
        else:
            device_type = "cpu"
        state_dict = safetensors.torch.load_file(model_full_path, device=device_type)
        biRefNet_model.load_state_dict(state_dict)
        biRefNet_model.to(device_type)
        biRefNet_model.eval()
        return biRefNet_model,


class LoadRembgByBiRefNetModel:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (folder_paths.get_filename_list(models_dir_key),),
                "device": (["AUTO", "CPU"], )
            }
        }

    RETURN_TYPES = ("BiRefNetMODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_model"
    CATEGORY = "rembg/BiRefNet"
    DESCRIPTION = "Load BiRefNet model from folder models/BiRefNet or the path of birefnet configured in the extra YAML file"

    def load_model(self, model, device):
        biRefNet_model = BiRefNet(bb_pretrained=False, bb_index=6)
        model_path = folder_paths.get_full_path(models_dir_key, model)
        if device == "AUTO":
            device_type = deviceType
        else:
            device_type = "cpu"
        if model_path.endswith(".safetensors"):
            state_dict = safetensors.torch.load_file(model_path, device=device_type)
        else:
            state_dict = torch.load(model_path, map_location=device_type)
            state_dict = check_state_dict(state_dict)

        biRefNet_model.load_state_dict(state_dict)
        biRefNet_model.to(device_type)
        biRefNet_model.eval()
        return [biRefNet_model]


class RembgByBiRefNet:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("BiRefNetMODEL",),
                "images": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK",)
    RETURN_NAMES = ("image", "mask",)
    FUNCTION = "rem_bg"
    CATEGORY = "rembg/BiRefNet"

    def rem_bg(self, model, images):
        _images = []
        _masks = []

        for image in images:
            h, w, c = image.shape
            pil_image = tensor_to_pil(image)

            im_tensor = proc_img(pil_image).unsqueeze(0)

            with torch.no_grad():
                mask = model(im_tensor.to(deviceType))[-1].sigmoid().cpu()

            # 遮罩大小需还原为与原图一致
            mask = comfy.utils.common_upscale(mask, w, h, 'bilinear', "disabled")

            # image的mask对应部分设为透明
            image = apply_mask_to_image(image.cpu(), mask.cpu())

            _images.append(image)
            _masks.append(mask)

        out_images = torch.cat(_images, dim=0)
        out_masks = torch.cat(_masks, dim=0)

        return out_images, out_masks


NODE_CLASS_MAPPINGS = {
    "LoadBiRefNetModelByName": AutoDownloadBiRefNetModel,
    "LoadRembgByBiRefNetModel": LoadRembgByBiRefNetModel,
    "RembgByBiRefNet": RembgByBiRefNet,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadBiRefNetModelByName": "LoadBiRefNetModelByName",
    "LoadRembgByBiRefNetModel": "LoadRembgByBiRefNetModel",
    "RembgByBiRefNet": "RembgByBiRefNet",
}
