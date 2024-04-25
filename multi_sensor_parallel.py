import itertools
import random
import time
import datetime
import openpyxl
import pandas as pd
import numpy as np
from joblib import Parallel, delayed
from multi_main import RunModel
from multi_data import Dataprep, update_data
from tqdm import tqdm

# set seeds for reproducibility
random.seed(42)
np.random.seed(42)

#324 x kernel
hyperparameter_spaces = {
                         'SVR': {'acquisition_function': ['RS'], 'kernel': ['rbf', 'sigmoid', 'poly'], 'C': [1, 5, 10], 'epsilon': [0.01, 0.05, 0.1]}, }
                         #'GP': {'learning_rate': [0.01, 0.1], 'kernel': ['Matern', 'Periodic'], 'lengthscale_prior': [None], 'lengthscale_sigma': [0.01, 0.1, 1], 'lengthscale_mean': [1.0], 'noise_prior': [None], 'noise_sigma': [0.01, 0.1, 0.5], 'noise_mean': [1.0], 'noise_constraint': [1e-6], 'lengthscale_type': ['Single', 'ARD'], 'acquisition_function': ['US', 'UCB', 'RS'], 'reg_lambda': [0.001, 0.01, 0.05]}, }#36 # RBF*Matern promising
#only if time:           #'GP': {'learning_rate': [0.01, 0.05, 0.1], 'kernel': ['RBF+Linear', 'RBF+Periodic', 'RBF*Periodic', 'RBF*Linear', 'RBF+Matern','RBF*Matern','Matern+Linear','Matern*Linear', 'Matern*Periodic', 'Periodic*Linear', 'Periodic+Linear'], 'lengthscale_prior': [None], 'lengthscale_sigma': [0.01, 0.05, 0.1, 0.5, 1], 'lengthscale_mean': [1.0], 'noise_prior': [None], 'noise_sigma': [0.01, 0.05, 0.1, 0.2, 0.5], 'noise_mean': [1.0], 'noise_constraint': [1e-1, 1e-3, 1e-6], 'lengthscale_type': ['Single', 'ARD'], 'acquisition_function': ['US', 'UCB', 'RS'], 'reg_lambda': [0.001, 0.01, 0.05]}, }#36 # RBF*Matern promising

directory = 'runs' + '_' + datetime.datetime.now().strftime("%m-%d %H:%M") # Directory to save the results
plot = False # Whether to plot the results in the last step
steps = 20 # Number of steps to run the active learning algorithm for
epochs = 100 # Number of epochs to train the model for
num_combinations = 4  # Number of random combinations to generate for each model (random search), set to really high value for grid search

sensors = ['49', '52', '59', '60', '164', '1477', '1493', '1509', '1525', '1541', '1563', '2348']
all_combinations = []
total_models = len(hyperparameter_spaces)
for model_name, params_space in hyperparameter_spaces.items():
    model_combinations = [dict(zip(params_space, v)) for v in itertools.product(*params_space.values())]
    if len(model_combinations) > num_combinations:
        model_combinations = random.sample(model_combinations, num_combinations)
    all_combinations.extend((model_name, combo) for combo in model_combinations)

print(f"Total models: {total_models}")
print(all_combinations)


def process_combination(model_name, combination, sensors, steps, epochs, directory, plot):            
    data = Dataprep(sensors=sensors, scaling='Minmax', initial_samplesize=36)
    #print('Data pool:', data.X_pool.shape, data.Y_pool.shape)
    #print('Data known:', data.X_selected.shape, data.Y_selected.shape)
    combination_id = f"{model_name}_{'_'.join([''.join([part[0] for part in k.split('_')]) + '_' + str(v) for k, v in combination.items()])}"

    model_instances = []
    for index, sensor in enumerate(sensors):
        run_name = f"{sensor}_{combination_id}"
        model = RunModel(model_name=model_name, run_name=run_name, directory=directory, x_total=data.X, y_total=data.Y[:, index], steps=steps, epochs=epochs, **combination)
        model_instances.append(model)

    xyz = []
    selected_indices = []
    with tqdm(total=steps, desc="Steps (all sensors)", leave=False, position=2) as pbar_steps:
        
        for step in range(steps):
            try:
                #print('Step:', step)
                #print(selected_indices)
                #print(len(set(selected_indices)))

                data.update_data(selected_indices) # Update the known and pool data

                step_data = []
                selected_indices = []
                for index, model in enumerate(model_instances):
                    #print(model.run_name)
                    topk = int(36/len(model_instances)) # Number of samples to select from the pool data per sensor/model

                    tqdm.write(f'------------ Step {step+1} for sensor {model.run_name.split("_")[0]} ------------')

                    # Train the model
                    train_model_time = time.time()
                    model.train_model(step=step, X_selected=data.X_selected, y_selected=data.Y_selected[:, index])
                    tqdm.write(f'---Training time: {time.time() - train_model_time:.2f} seconds')

                    # Get the final predictions as if this was the last step
                    final_prediction_time = time.time()
                    x_highest_pred_n, y_highest_pred_n, x_highest_actual_n, y_highest_actual_n, x_highest_actual_1, y_highest_actual_1, mse, mae, percentage_common, index_of_actual_1_in_pred, seen_count, highest_actual_in_top, highest_indices_pred, highest_indices_actual_1 = model.final_prediction(step=step, X_total=data.X, y_total=data.Y[:, index], X_selected=data.X_selected, topk=topk)
                    #print(model_name)
                    #print('MSE:', mse, 'MAE:', mae, 'Percentage common:', percentage_common, 'Highest actual in top:', highest_actual_in_top, 'Highest actual in known:', highest_actual_in_known)
                    tqdm.write(f'---Final prediction time: {time.time() - final_prediction_time:.2f} seconds')

                    # Evaluate the model on the pool data
                    evaluate_pool_data_time = time.time()
                    model.evaluate_pool_data(step=step, X_pool=data.X_pool, y_pool=data.Y_pool[:, index]) 
                    tqdm.write(f'---Evaluation on pool data time: {time.time() - evaluate_pool_data_time:.2f} seconds')

                    # Predict the uncertainty on the pool data
                    predict_time = time.time()
                    means, stds = model.predict(X_pool=data.X_pool)
                    tqdm.write(f'---Prediction time: {time.time() - predict_time:.2f} seconds')

                    # Select the next samples from the pool
                    acquisition_function_time = time.time()
                    selected_indices = model.acquisition_function(means, stds, y_selected=data.Y_selected[:, index], X_Pool=data.X_pool, topk=topk, selected_indices=selected_indices)
                    tqdm.write(f'---Acquisition function time: {time.time() - acquisition_function_time:.2f} seconds')
                    #print('length of selected indices:', selected_indices)
                    #print('training data (y):', data.Y_selected.shape, data.Y_selected[:5, index])

                    step_data.append({
                                    'Step': step+1,
                                    'Model': model.run_name.split('_')[1],
                                    'Sensor': model.run_name.split('_')[0],
                                    'Combination': model.run_name.split('_', 2)[2],
                                    'MSE': mse,
                                    'MAE': mae,
                                    'Percentage_common': percentage_common,
                                    'Index of highest simulation': index_of_actual_1_in_pred,
                                    'Simulations seen before': seen_count,
                                    'Highest simulation in pred': highest_actual_in_top,
                                    'highest_indices_pred': highest_indices_pred,
                                    'highest_indices_actual_1': highest_indices_actual_1,
                    })

                    if plot and (step + 1) % 5 == 0:
                        plot_time = time.time()
                        model.plot(means=means, stds=stds, selected_indices=selected_indices[-topk:], step=step, x_highest_pred_n=x_highest_pred_n, y_highest_pred_n=y_highest_pred_n, x_highest_actual_n=x_highest_actual_n, y_highest_actual_n=y_highest_actual_n, x_highest_actual_1=x_highest_actual_1, y_highest_actual_1=y_highest_actual_1, X_pool=data.X_pool, y_pool=data.Y_pool[:, index], X_selected=data.X_selected, y_selected=data.Y_selected[:, index])
                        tqdm.write(f'---Plotting time: {time.time() - plot_time:.2f} seconds')
                    
                    tqdm.write(f'------------ Step {step+1} completed ------------')

            except Exception as e:
                print(f'Error in step {step+1} for sensor {model.run_name.split("_")[0]}: {e}')
                step_data.append({
                                    'Step': step+1,
                                    'Model': model.run_name.split('_')[1],
                                    'Sensor': model.run_name.split('_')[0],
                                    'Combination': model.run_name.split('_', 2)[2],
                                    'MSE': None,
                                    'MAE': None,
                                    'Percentage_common': None,
                                    'Index of highest simulation': None,
                                    'Simulations seen before': None,
                                    'Highest simulation in pred': None,
                                    'highest_indices_pred': None,
                                    'highest_indices_actual_1': None,
                    })
                continue
            xyz.append(step_data)
            pbar_steps.update(1)
        return xyz

# Calculate the start time
start = time.time()

steps_dataframes = {} 

# Parallelize processing of each combination
results = Parallel(n_jobs=-1)(delayed(process_combination)(model_name, combo, sensors, steps, epochs, directory, plot) for model_name, combo in all_combinations)

excel_filename = f'{directory}/{model_name}_model_results.xlsx'
# Aggregating results into steps_dataframes
for result in results:
    for data in result:
        for row in data:
            step = row['Step']  # Assuming 'Step' is a key in your returned dictionaries
            if step in steps_dataframes:
                steps_dataframes[step].append(row)
            else:
                steps_dataframes[step] = [row]

# Now converting lists of dictionaries to dataframes and writing to Excel
with pd.ExcelWriter(excel_filename, engine='openpyxl') as writer:
    for step, data_list in steps_dataframes.items():
        df = pd.DataFrame(data_list)
        sheet_name = f'Step_{step}'
        df.to_excel(writer, sheet_name=sheet_name, index=False)

# Calculate the end time and time taken
end = time.time()
length = end - start

# Show the results : this can be altered however you like
print("It took", length, "seconds to run", total_models, "models for", steps, "steps")