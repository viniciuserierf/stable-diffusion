import torch

from ldm.controlnet import load_controlnet as load_controlnet_cldm
from ldm.sample import prepare_noise, sample

from ldm.model_management import unload_all_models
from ldm.lora import load_lora_for_models
from ldm.sd import load_checkpoint_guess_config

import copy
from PIL import Image, ImageOps
import numpy as np
import torch


# returns a conditioning with a controlnet applied to it, ready to pass it to a KSampler
def apply_controlnet(conditioning, control_net, image, strength):
    if strength == 0:
        return (conditioning,)

    c = []
    control_hint = image.movedim(-1, 1)
    for t in conditioning:
        n = [t[0], t[1].copy()]
        c_net = control_net.copy().set_cond_hint(control_hint, strength)
        if "control" in t[1]:
            c_net.set_previous_controlnet(t[1]["control"])
        n[1]["control"] = c_net
        n[1]["control_apply_to_uncond"] = True
        c.append(n)
    return (c,)


def load_image(image):
    i = ImageOps.exif_transpose(image)
    image = i.convert("RGB")
    image = np.array(image).astype(np.float32) / 255.0
    image = torch.from_numpy(image)[None,]
    if "A" in i.getbands():
        mask = np.array(i.getchannel("A")).astype(np.float32) / 255.0
        mask = 1.0 - torch.from_numpy(mask)
    else:
        mask = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
    return (image, mask.unsqueeze(0))


def load_controlnet(
    controlnet_model,
    conditioning_img,
    width,
    height,
    strength,
    model_file,
    device,
    conditioning,
    negative_conditioning,
    raw_loras=[],
    unet_dtype=torch.float16,
):
    # Load controlnet model
    controlnet = load_controlnet_cldm(controlnet_model)

    # Load conditioning image
    (image, _mask) = load_image(Image.open(conditioning_img).resize((width, height), resample=Image.Resampling.BILINEAR))

    # Load base model
    out = load_checkpoint_guess_config(
        model_file,
        output_vae=False,
        output_clip=False,
        output_clipvision=False,
    )
    
    model_patcher = out[0]
    
    # model_config = model_config_from_unet(
    #     state_dict, "model.diffusion_model.", unet_dtype
    # )

    # # Set the weights
    # sd_model = model_config.get_model(
    #     state_dict,
    #     "model.diffusion_model.",
    #     device=device,
    # )
    # sd_model.load_model_weights(state_dict, "model.diffusion_model.")

    # # Create the comfy model
    # model_patcher = ModelPatcher(
    #     sd_model,
    #     load_device=device,
    #     current_device=device,
    #     offload_device=torch.device("cpu"),
    # )

    # # Move model to GPU
    # load_model_gpu(model_patcher)

    # Apply loras
    lora_model_patcher = model_patcher

    for lora in raw_loras:
        print(lora["weight"])
        lora_model_patcher, _clip = load_lora_for_models(
            lora_model_patcher, None, lora["sd"], lora["weight"] / 100, 0
        )

    # Compute conditioning
    cldm_conditioning = [[conditioning[0], {"pooled_output": None}]]
    cldm_negative_conditioning = [[negative_conditioning[0], {"pooled_output": None}]]

    # Apply controlnet to conditioning
    (controlled_conditioning,) = apply_controlnet(
        cldm_conditioning, controlnet, image, strength
    )

    return lora_model_patcher, controlled_conditioning, cldm_negative_conditioning


def sample_cldm(
    model_patcher,
    conditioning,
    negative_conditioning,
    seed,
    steps = 20,
    cfg = 5.0,
    sampler = "euler",
    batch=1,
    width=512,
    height=512,
    latent=None,
    scheduler = "normal",
):
    # Generate empty latents for txt2img
    if latent is None:
        latent = torch.zeros([batch, 4, height // 8, width // 8])

    # Prepare noise
    noise = prepare_noise(latent, seed, None)
    
    for samples_cldm in sample(
        model_patcher,
        noise,
        steps,
        cfg,
        sampler,
        scheduler,
        conditioning,
        negative_conditioning,
        latent,
        seed=seed,
    ):
        yield samples_cldm / 6.0

def unload_cldm():
    # Unload the model
    unload_all_models()
    
    return