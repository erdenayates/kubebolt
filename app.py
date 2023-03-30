from flask import Flask, render_template, request, redirect
from kubernetes import client, config
from datetime import datetime

# Configure Kubernetes API client
config.load_incluster_config()

# Initialize Flask app
app = Flask(__name__, static_folder="static")

@app.route("/")
def index():
    deployments = get_deployments()
    deployment_pods = {}
    for deployment in deployments.items:
        namespace = deployment.metadata.namespace
        deployment_name = deployment.metadata.name
        deployment_pods[deployment_name] = get_pods_by_deployment(namespace, deployment_name)

    node_metrics = get_node_metrics()
    node_metrics_human_readable = []

    if node_metrics:
        for node_metric in node_metrics['items']:
            node_name = node_metric['metadata']['name']
            cpu_usage_raw = node_metric['usage']['cpu']
            memory_usage_raw = node_metric['usage']['memory']
            memory_capacity = node_metric['memory_capacity']

            cpu_usage = float(cpu_usage_raw.strip('n')) / 1e6  # Convert nanocores to millicores
            memory_usage = float(memory_usage_raw.strip('Ki')) * 1024  # Convert kibibytes to bytes

            memory_usage_percentage = (memory_usage / memory_capacity) * 100

            node_metrics_human_readable.append({
                'name': node_name,
                'cpu_usage': round(cpu_usage, 2),
                'memory_usage': int(memory_usage),
                'memory_usage_percentage': round(memory_usage_percentage, 2)
            })

    return render_template("index.html", deployments=deployments, deployment_pods=deployment_pods, node_metrics=node_metrics_human_readable)


@app.route("/scale", methods=["POST"])
def scale():
    namespace = request.form["namespace"]
    deployment_name = request.form["deployment_name"]
    replicas = int(request.form["replicas"])

    scale_deployment(namespace, deployment_name, replicas)

    return redirect("/")

@app.route("/logs", methods=["POST"])
def logs():
    namespace = request.form["namespace"]
    pod_name = request.form["pod_name"]

    logs = get_pod_logs(namespace, pod_name)
    return render_template("logs.html", logs=logs)

@app.route("/fetch_logs", methods=["POST"])
def fetch_logs():
    namespace = request.form["namespace"]
    pod_name = request.form["pod_name"]

    logs = get_pod_logs(namespace, pod_name)
    return logs

@app.route("/delete-error-completed-pods", methods=["POST"])
def delete_error_completed_pods():
    delete_error_and_completed_pods()
    return redirect("/")

@app.route("/restart", methods=["POST"])
def rollout_restart_deployment():
    namespace = request.form["namespace"]
    deployment_name = request.form["deployment_name"]
    
    rollout_restart(namespace, deployment_name)
    
    return redirect("/")

def rollout_restart(namespace, deployment_name):
    api_instance = client.AppsV1Api()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
                    }
                }
            }
        }
    }
    api_instance.patch_namespaced_deployment(deployment_name, namespace, body)

def get_deployments():
    api_instance = client.AppsV1Api()
    deployments = api_instance.list_deployment_for_all_namespaces()
    return deployments

def scale_deployment(namespace, deployment_name, replicas):
    api_instance = client.AppsV1Api()

    # Update the deployment with the new replica count
    update_deployment = {
        'spec': {
            'replicas': replicas
        }
    }

    try:
        api_response = api_instance.patch_namespaced_deployment(deployment_name, namespace, update_deployment)
        print(f"Deployment {deployment_name} in namespace {namespace} has been scaled to {replicas} replicas.")
    except client.ApiException as e:
        print(f"Exception when calling AppsV1Api->patch_namespaced_deployment: {e}")


def get_pod_logs(namespace, pod_name):
    api_instance = client.CoreV1Api()
    logs = api_instance.read_namespaced_pod_log(pod_name, namespace)
    return logs

def delete_error_and_completed_pods():
    api_instance = client.CoreV1Api()
    pods = api_instance.list_pod_for_all_namespaces()

    for pod in pods.items:
        if pod.status.phase in ['Error', 'Succeeded', 'Failed']:
            api_instance.delete_namespaced_pod(pod.metadata.name, pod.metadata.namespace)

def get_pods_by_deployment(namespace, deployment_name):
    api_instance = client.CoreV1Api()

    # Get deployment to find the correct label selector
    apps_v1_api = client.AppsV1Api()
    deployment = apps_v1_api.read_namespaced_deployment(namespace=namespace, name=deployment_name)

    # Use the deployment's label selector to find its pods
    label_selector = ','.join([f"{k}={v}" for k, v in deployment.spec.selector.match_labels.items()])
    pods = api_instance.list_namespaced_pod(namespace, label_selector=label_selector)
    return pods

def get_node_metrics():
    api_instance = client.CustomObjectsApi()
    group = 'metrics.k8s.io'
    version = 'v1beta1'
    plural = 'nodes'

    try:
        node_metrics = api_instance.list_cluster_custom_object(group, version, plural)
        core_v1_api = client.CoreV1Api()

        for node_metric in node_metrics['items']:
            node_name = node_metric['metadata']['name']
            node = core_v1_api.read_node(node_name)
            node_memory_capacity_raw = node.status.capacity['memory']

            node_memory_capacity = float(node_memory_capacity_raw.strip('Ki')) * 1024  # Convert kibibytes to bytes
            node_metric['memory_capacity'] = node_memory_capacity

        return node_metrics
    except ApiException as e:
        print(f"Exception when calling CustomObjectsApi->list_cluster_custom_object: {e}")
        return None



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
