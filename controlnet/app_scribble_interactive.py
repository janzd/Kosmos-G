from app_utils import *


def create_canvas(w, h):
    return np.zeros(shape=(h, w, 3), dtype=np.uint8) + 255


def create_demo_scribble_interactive(generation_fn):
    with gr.Blocks() as demo:
        with gr.Row():
            with gr.Column(scale=1):
                canvas_width = gr.Slider(label='Canvas width',
                                         minimum=256,
                                         maximum=MAX_IMAGE_RESOLUTION,
                                         value=DEFAULT_IMAGE_RESOLUTION,
                                         step=1)
                canvas_height = gr.Slider(label='Canvas height',
                                          minimum=256,
                                          maximum=MAX_IMAGE_RESOLUTION,
                                          value=DEFAULT_IMAGE_RESOLUTION,
                                          step=1)
                create_button = gr.Button('Open drawing canvas!')
                image = gr.Image(tool='sketch', brush_radius=10)
                prompt = gr.Textbox(label="Prompt", max_lines=1,
                                    placeholder="Use <i> to represent the images in prompt")
                num_input_images = gr.Slider(1, MAX_INPUT_IMAGES, value=DEFAULT_INPUT_IMAGES, step=1,
                                             label="Number of input images:")
                input_images = [
                    gr.Image(label=f'img{i}', type="pil", visible=True if i < DEFAULT_INPUT_IMAGES else False)
                    for i in range(MAX_INPUT_IMAGES)]
                num_input_images.change(variable_images, num_input_images, input_images)

                seed = gr.Slider(label="Seed", minimum=MIN_SEED, maximum=MAX_SEED, step=1, value=0)
                randomize_seed = gr.Checkbox(label='Randomize seed', value=True)
                run_button = gr.Button(label="Run")
                with gr.Accordion("Advanced options", open=False):
                    num_inference_steps = gr.Slider(label="num_inference_steps", minimum=10, maximum=100, value=50,
                                                    step=5)
                    text_guidance_scale = gr.Slider(1, 15, value=6, step=0.5, label="Text Guidance Scale")
                    negative_prompt = gr.Textbox(label="Negative Prompt", max_lines=1,
                                                 value="")
                    num_images_per_prompt = gr.Slider(1, MAX_IMAGES_PER_PROMPT, value=DEFAULT_IMAGES_PER_PROMPT, step=1,
                                                      label="Number of Images")
                    image_resolution = gr.Slider(label='Image resolution', minimum=MIN_IMAGE_RESOLUTION,
                                                 maximum=MAX_IMAGE_RESOLUTION, value=DEFAULT_IMAGE_RESOLUTION, step=256)

            with gr.Column(scale=2):
                result_gallery = gr.Gallery(label='Output', show_label=False, elem_id="gallery", columns=2,
                                            height='100%')
        create_button.click(
            fn=create_canvas,
            inputs=[canvas_width, canvas_height],
            outputs=image,
            queue=False,
            api_name=False,
        )
        ips = [prompt, num_inference_steps, text_guidance_scale, negative_prompt, num_images_per_prompt, image,
               image_resolution, *input_images]

        prompt.submit(
            fn=randomize_seed_fn, inputs=[seed, randomize_seed], outputs=seed, queue=False, api_name=False
        ).then(fn=generation_fn, inputs=ips, outputs=result_gallery)

        run_button.click(
            fn=randomize_seed_fn, inputs=[seed, randomize_seed], outputs=seed, queue=False, api_name=False
        ).then(fn=generation_fn, inputs=ips, outputs=result_gallery)

        gr.Examples(
            examples=controlnet_example,
            inputs=[image, prompt, input_images[0], input_images[1]],
            cache_examples=False,
            examples_per_page=100
        )

    return demo
